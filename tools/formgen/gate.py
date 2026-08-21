#!/usr/bin/env python3
"""The done-condition for tools/formgen/GOAL.md. Exit 0 means finished.

One command, so nobody has to decide whether the work is done by reading a
summary. Every check prints a line and a verdict.

The rule that matters most here: **a check that cannot be evaluated is a
failure, never a pass.** This project has already been burned by the opposite.
The numeric audit reported `rules 100% on 51/51` while 137 real defects were
present -- a black rectangle over a header, a seal printed upside-down, tax
brackets a taxpayer could type over -- because it only compared what it knew to
compare. An unimplemented assertion that silently passes is that same failure
wearing a green tick, so `UNEVALUABLE` is counted with the failures and named in
the summary.

Usage:
    python3 tools/formgen/gate.py                 # the real gate
    python3 tools/formgen/gate.py --only rules    # one check, while iterating
    python3 tools/formgen/gate.py --list          # what the checks are
    python3 tools/formgen/gate.py --self-test     # the gate checks itself
"""

from __future__ import annotations

import argparse
import ctypes
import dataclasses
import enum
import errno
import hashlib
import html.parser
import importlib.machinery
import importlib.metadata
import json
import math
import mimetypes
import os
import pathlib
import platform
import posixpath
import re
import shutil
import signal
import stat
import subprocess
import sys
import sysconfig
import tempfile
import time
import urllib.parse
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from typing import Any, Callable, Iterable, Sequence

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent

FORMS = REPO / "forms"
BUILD = REPO / "build"
AUDIT_JSON = BUILD / "audit.json"
AUDIT_APPLICATION_ATTESTATION = BUILD / "audit-attested.json"
BATCH_REPORT = BUILD / "batch-report.json"
FINDINGS = HERE / "review-findings.json"
COMB_REFEREE_REPORT = BUILD / "comb-referee.json"
COMB_REFEREE_ATTESTATION = BUILD / "comb-referee-attested.json"
COMB_REFEREE_SOURCE_ROOT = pathlib.Path.home() / "Downloads/forms"
# Stage 2 (ARCHITECTURE.md). The corrected tree, its ledger, and the report a
# fidelity run over that tree must write. The last of the three DOES NOT EXIST
# yet and is named here on purpose: `check_corrected_tree` fails when a
# divergence is declared and no report publishes it, so naming the path is what
# makes that failure legible instead of mysterious.
CORRECTED_TREE = REPO / "forms-corrected"
CORRECTED_MANIFEST = REPO / "forms-corrected.manifest.json"
CORRECTED_FIDELITY_REPORT = BUILD / "corrected-fidelity.json"
# The stage-2 ledger. Read here for ONE fact: whether any correction has been
# declared. `correct.py` owns what a record means; this only needs to know that
# a tree is owed. The scan is the applier's rule restated, not imported -- root
# only, because `evidence/` and `schema/` deliberately sit where the applier's
# loader never looks and recursing would count backing documents as records.
CORRECTIONS_LEDGER = HERE / "corrections"

# Corpus census. Pins, not thresholds: a form that appears or disappears has to
# be declared here, in a commit that says which one and why. 51 -> 53 and
# extra 13 -> 15 is 1604-CF and 2200-AN arriving -- two forms on BIR's official
# list that had no local source PDF until they were fetched from the BIR CDN.
# Both land under forms/extra/ because neither is in the reviewed in-corpus set.
EXPECTED_FORMS = 53
EXPECTED_IN_CORPUS_FORMS = 38
EXPECTED_EXTRA_FORMS = 15
# The same quantity comb_referee.EXPECTED_COMBS pins, in a second file. It went
# stale at 4442 while the referee's moved to 4540 -- 98 comb subjects created by
# the comb writing-surface and painted-wall fixes -- and nothing noticed, because
# only a FULL gate run reads this one. Two files pinning one number is the
# defect; until they are unified, changing either means changing both.
# 4540 -> 4521 (2026-08-07, r14), moved together with comb_referee.EXPECTED_COMBS
# and its 13 per-slug values. The cause is 21e0630's shaded-paper fix, which was
# committed without its census: the HEAD lattice already produced 4,521 before
# this session touched anything. See the note on comb_referee.EXPECTED_COMBS.
# 4521 -> 4543 (2026-08-07, r20). TWO causes, and the first one was already live
# at HEAD: r19 corrected comb_referee.EXPECTED_COMBS to 4,538 and did NOT move
# this twin, so validate_comb_referee_report was comparing the referee's
# `combs_expected` of 4,538 against 4,521 and could only ever have failed. That
# is the very defect this comment describes, repeating in the same pair of
# files, one revision later. The second cause is r20's own: extract.py's
# line-cap model takes the measured ledger denominator to 4,543.
# 4543 -> 4583 (2026-08-07, r21), moved in the same commit as its twin
# comb_referee.EXPECTED_COMBS: lattice.py's bottom-guide-tick recognition adds
# 40 subjects on nine slugs (see the cause note at that pin).
# 4583 -> 4587 on 2026-08-10 (r41): F201/P1b bridges the legacy lattice too, so
# 2200C gains 3 comb subjects and 2000-DST 1. Zero cells move; see comb_referee's
# LATTICE_PRODUCER_SHA256 note.
EXPECTED_COMB_SUBJECTS = 4587
COMB_REFEREE_REPORT_VERSION = 2
COMB_REFEREE_ATTESTATION_VERSION = 2
COMB_REFEREE_SCOPE = "formgen-comb-referee-application-v1"
AUDIT_APPLICATION_SCOPE = "formgen-audit-application-v2"
AUDIT_APPLICATION_ATTESTATION_VERSION = 2
COMB_REFEREE_TIMEOUT_SECONDS = 7200
COMB_REFEREE_RUN_COUNT = 2
COMB_REFEREE_CLEANUP_TIMEOUT_SECONDS = 5
COMB_REFEREE_TOTAL_TIMEOUT_SECONDS = (
    COMB_REFEREE_RUN_COUNT * (
        COMB_REFEREE_TIMEOUT_SECONDS
        + 2 * COMB_REFEREE_CLEANUP_TIMEOUT_SECONDS))
ISOLATED_PYTHON_ATTESTED_FLAGS = [
    "-I", "-S", "-B", "-X", "pycache_prefix=<fresh-empty-directory>",
]
AUDIT_ROUNDTRIP_LAUNCH_ARGS = [
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-sync",
    "--metrics-recording-only",
    "--no-first-run",
]
AUDIT_ROUNDTRIP_SCOPE = (
    "playwright-package-tree-and-explicit-chromium-executable")
AUDIT_CANDIDATE_MATERIALIZATION = (
    "private-0700-o_excl-o_nofollow-fsynced-unlinked-read-fd")
AUDIT_PDF_NORMALIZATION_REPLACEMENT = "D:19700101000000+00'00'"
ISOLATED_DEPENDENCY_PACKAGES = {
    "pymupdf": ("pymupdf", "fitz"),
    "playwright": ("playwright",),
    "pyee": ("pyee",),
    "greenlet": ("greenlet",),
    "typing-extensions": ("typing_extensions.py",),
}
COMB_REFEREE_PRODUCERS = (
    "tools/formgen/gate.py",
    "tools/formgen/batch.py",
    "tools/formgen/comb_referee.py",
    "tools/formgen/audit.py",
    "tools/formgen/lattice.py",
    "tools/formgen/extract.py",
    "tools/formgen/guides.py",
    "tools/formgen/fonts.py",
    "tools/formgen/emit.py",
    "tools/formgen/index_page.py",
    "tools/formgen/verify.py",
)
COMB_REFEREE_ARTIFACT_TREES = {
    "ir": BUILD / "ir",
    "layout": BUILD / "layout",
    "html": BUILD / "html",
    "guides": BUILD / "guides",
}
COMPARISON_NAMES = (
    # `excepted` (S2) is its own kind and is NEVER folded into `agree`: the
    # report must always state how many verdicts a reviewer excused, and the
    # pass bar below has to name them explicitly rather than inherit them.
    "agree", "excepted", "repair-lattice", "repair-audit",
    "stale-generation", "stop", "unevaluable",
)

# Modules that expose --self-test. lattice and fonts need an --ir argument, so
# they are invoked with one rather than being excused from the check.
SELF_TEST_MODULES = (
    "extract", "lattice", "fonts", "guides", "emit", "verify", "index_page",
    "audit", "comb_referee", "gate", "field_identity", "map_tin",
)
# Identity coverage pin (I3). Must match field_identity.EXPECTED_FILLABLE_CELLS.
EXPECTED_FILLABLE_CELLS = 9990
EXPECTED_UNCATALOGUED_FILLABLES = 0
SELF_SUPERVISING_SELF_TEST_MODULES = frozenset({"comb_referee", "gate"})

# A checker-of-checkers, not a module self-test: it proves every `extract.py`
# check is reachable from a SOURCE-LEVEL mutation -- a change to a fixture PDF's
# own content stream that makes the check fail -- or carries a stated reason it
# cannot be. It lives under `fixtures/` and takes no `--self-test` flag, so the
# module loop above cannot reach it.
#
# Added 2026-08-10, and the reason is the point: three ruled-blank checks landed
# unproven and survived gates r37 and r38 GREEN, because this script ran only in
# CI. A gate that scores ten modules' self-tests while the thing that proves
# those tests can fail runs somewhere else is a gate with a hole in it. It was
# found by chance -- an agent ran the script unprompted -- which is not a
# detection mechanism.
PROVE_FIXTURES_SCRIPT = "fixtures/prove_fixtures_fail.py"

# The assertions the gate demands. gate.py does not implement them: audit.py
# owns them, and the gate's job is to demand them. Each maps to the key audit.py
# must publish per form in its record.
#
# GOAL.md named eight. The last two are G10's, and they exist because the other
# eight are structurally blind to the FIELD layer: 171 of 172 ledger findings
# carry `audit_blind: true`, and a 51-form visual sweep found 138 defects of
# which 137 sat on pages this gate scored 100% rules / 100% text / 0 missing /
# 0 extra. The two assertions that came closest each take their candidate
# population from the producer that made the mistake -- `money_boxes_have_inputs`
# enumerates from the layout's `field` cells, `comb_slots_match_printed` from
# the layout's comb subjects -- so a printed box the lattice mis-read is not in
# either population. The two below take their population from the pinned PDF.
REQUIRED_ASSERTIONS = {
    "inputs_over_printed_text": "No <input> overlaps a pre-printed text run's bbox",
    "comb_slots_match_printed": "Every comb's slot count equals its printed compartment count",
    "money_boxes_have_inputs": "Every printed money box on a form page has an input",
    "rules_below_guide_cut": "No form-side rule extends below that page's guide cut",
    "run_colour_matches_ir": "No emitted run's colour differs from the IR's",
    "reflow_rate_without_description": "No relocated table row has an empty description and a rate",
    "image_transform_applied": "Every non-positive-diagonal image transform is emitted",
    "no_invented_codepoints": "No IR run holds a character the source did not state",
    "inputs_span_no_printed_divider": "No <input> spans a compartment divider the source printed inside it",
    "printed_box_peers_all_fillable": "No printed box lacks an input while an identical row peer has one",
}
AUDIT_DEPENDENT_CHECKS = {
    "rules", "paper", "artwork", "text", "assertions",
}


class Verdict(enum.Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNEVALUABLE = "UNEVALUABLE"   # counted as a failure; see the module docstring

    @property
    def ok(self) -> bool:
        return self is Verdict.PASS


@dataclasses.dataclass
class Result:
    name: str
    verdict: Verdict
    detail: str


def run(args: list[str], timeout: int = 5400) -> tuple[int, str]:
    return run_isolated_python(args, timeout)


def _normalise_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _approved_dependency_roots() -> tuple[pathlib.Path, ...]:
    """Return interpreter-derived package roots without importing ``site``."""
    candidates: list[str | None] = [
        sysconfig.get_path("purelib"),
        sysconfig.get_path("platlib"),
    ]
    schemes = set(sysconfig.get_scheme_names())
    for scheme in ("osx_framework_user", "posix_user", "nt_user"):
        if scheme not in schemes:
            continue
        candidates.extend((
            sysconfig.get_path("purelib", scheme=scheme),
            sysconfig.get_path("platlib", scheme=scheme),
        ))
    roots: list[pathlib.Path] = []
    for value in candidates:
        if not value:
            continue
        path = pathlib.Path(value).resolve()
        if path.is_dir() and path not in roots:
            roots.append(path)
    return tuple(roots)


def _distribution_metadata_name(path: pathlib.Path) -> str | None:
    metadata = path / "METADATA"
    try:
        for line in metadata.read_text(encoding="utf-8").splitlines():
            if line.startswith("Name:"):
                return _normalise_distribution_name(line.partition(":")[2].strip())
    except (OSError, UnicodeError):
        return None
    return None


def _dependency_tree_records(
        source: pathlib.Path, kind: str,
        ) -> dict[str, dict[str, Any]]:
    source = source.absolute()
    source_parent = source.parent.resolve(strict=True)
    parent_fd = _open_absolute_directory(source_parent)
    try:
        return _dependency_tree_records_at(
            parent_fd, source.name, kind, str(source))
    finally:
        os.close(parent_fd)


def _dependency_open_flags(*, directory: bool) -> int:
    flags = (
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0))
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    return flags


def _open_absolute_directory(path: pathlib.Path | str) -> int:
    absolute = pathlib.Path(path)
    if not absolute.is_absolute():
        raise RuntimeError(
            f"isolated dependency root is not absolute: {absolute}")
    descriptor = os.open(
        os.path.sep, _dependency_open_flags(directory=True))
    try:
        for part in absolute.parts[1:]:
            if part in {"", ".", ".."} or os.path.sep in part:
                raise RuntimeError(
                    f"isolated dependency root is unsafe: {absolute}")
            child = os.open(
                part, _dependency_open_flags(directory=True),
                dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _fd_identity(item: os.stat_result) -> tuple[int, ...]:
    return (
        item.st_dev, item.st_ino, item.st_mode, item.st_size,
        item.st_mtime_ns, item.st_ctime_ns,
    )


def _hash_stable_fd(descriptor: int) -> tuple[int, str, int]:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode):
        raise RuntimeError("isolated dependency descriptor is not regular")
    digest = hashlib.sha256()
    total = 0
    while True:
        chunk = os.pread(descriptor, 1024 * 1024, total)
        if not chunk:
            break
        digest.update(chunk)
        total += len(chunk)
    after = os.fstat(descriptor)
    if (_fd_identity(before) != _fd_identity(after)
            or total != after.st_size):
        raise RuntimeError(
            "isolated dependency changed while hashing its open descriptor")
    return total, digest.hexdigest(), stat.S_IMODE(after.st_mode)


def _symlink_target_is_internal(relative: str, target: str) -> bool:
    target_path = pathlib.PurePosixPath(target)
    if target_path.is_absolute():
        return False
    stack = list(pathlib.PurePosixPath(relative).parent.parts)
    for part in target_path.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not stack:
                return False
            stack.pop()
        else:
            stack.append(part)
    return True


def _normalise_dependency_parts(parts: Iterable[str]) -> list[str] | None:
    stack: list[str] = []
    for part in parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not stack:
                return None
            stack.pop()
        else:
            stack.append(part)
    return stack


def _dependency_symlink_resolves(
        records: dict[str, dict[str, Any]], relative: str,
        ) -> bool:
    link = records.get(relative)
    if not isinstance(link, dict) or link.get("type") != "symlink":
        return False
    initial = _normalise_dependency_parts([
        *pathlib.PurePosixPath(relative).parent.parts,
        *pathlib.PurePosixPath(str(link.get("target", ""))).parts,
    ])
    if initial is None:
        return False
    pending = initial
    expanded: set[tuple[str, ...]] = set()
    for _ in range(len(records) + 1):
        resolved: list[str] = []
        restarted = False
        for index, part in enumerate(pending):
            candidate = "/".join([*resolved, part])
            record = records.get(candidate)
            if not isinstance(record, dict):
                return False
            if record.get("type") != "symlink":
                resolved.append(part)
                continue
            state = tuple([*resolved, part, *pending[index + 1:]])
            if state in expanded:
                return False
            expanded.add(state)
            target = record.get("target")
            if not isinstance(target, str):
                return False
            replacement = _normalise_dependency_parts([
                *resolved,
                *pathlib.PurePosixPath(target).parts,
                *pending[index + 1:],
            ])
            if replacement is None:
                return False
            pending = replacement
            restarted = True
            break
        if not restarted:
            final = "/".join(resolved)
            return final in records and records[final].get("type") in {
                "file", "directory"}
    return False


def _dependency_tree_records_at(
        parent_fd: int, name: str, kind: str, logical: str,
        ) -> dict[str, dict[str, Any]]:
    if kind not in {"file", "directory"}:
        raise RuntimeError(f"isolated dependency kind is invalid: {logical}")
    descriptor = os.open(
        name, _dependency_open_flags(directory=kind == "directory"),
        dir_fd=parent_fd)
    try:
        root_stat = os.fstat(descriptor)
        if kind == "file":
            byte_count, digest, mode = _hash_stable_fd(descriptor)
            return {"": {
                "path": "", "type": "file", "mode": mode,
                "bytes": byte_count, "sha256": digest,
            }}
        if not stat.S_ISDIR(root_stat.st_mode):
            raise RuntimeError(
                f"isolated dependency directory is absent: {logical}")
        records: dict[str, dict[str, Any]] = {
            "": {
                "path": "", "type": "directory",
                "mode": stat.S_IMODE(root_stat.st_mode),
            },
        }

        def visit(directory_fd: int, prefix: pathlib.PurePosixPath) -> None:
            for child_name in sorted(os.listdir(directory_fd)):
                relative = prefix / child_name
                relative_text = relative.as_posix()
                if ("__pycache__" in relative.parts
                        or relative.suffix == ".pyc"):
                    continue
                before = os.stat(
                    child_name, dir_fd=directory_fd,
                    follow_symlinks=False)
                if stat.S_ISLNK(before.st_mode):
                    target = os.readlink(child_name, dir_fd=directory_fd)
                    after = os.stat(
                        child_name, dir_fd=directory_fd,
                        follow_symlinks=False)
                    if (_fd_identity(before) != _fd_identity(after)
                            or not _symlink_target_is_internal(
                                relative_text, target)):
                        raise RuntimeError(
                            "isolated dependency symlink changed or escapes: "
                            f"{logical}/{relative_text} -> {target}")
                    records[relative_text] = {
                        "path": relative_text, "type": "symlink",
                        "target": target,
                    }
                    continue
                if stat.S_ISDIR(before.st_mode):
                    child_fd = os.open(
                        child_name, _dependency_open_flags(directory=True),
                        dir_fd=directory_fd)
                    try:
                        opened = os.fstat(child_fd)
                        if _fd_identity(before) != _fd_identity(opened):
                            raise RuntimeError(
                                "isolated dependency directory changed while "
                                f"opening: {logical}/{relative_text}")
                        records[relative_text] = {
                            "path": relative_text, "type": "directory",
                            "mode": stat.S_IMODE(opened.st_mode),
                        }
                        visit(child_fd, relative)
                        if _fd_identity(opened) != _fd_identity(
                                os.fstat(child_fd)):
                            raise RuntimeError(
                                "isolated dependency directory changed while "
                                f"scanning: {logical}/{relative_text}")
                    finally:
                        os.close(child_fd)
                    continue
                if not stat.S_ISREG(before.st_mode):
                    raise RuntimeError(
                        f"isolated dependency is not regular: "
                        f"{logical}/{relative_text}")
                child_fd = os.open(
                    child_name, _dependency_open_flags(directory=False),
                    dir_fd=directory_fd)
                try:
                    opened = os.fstat(child_fd)
                    if _fd_identity(before) != _fd_identity(opened):
                        raise RuntimeError(
                            "isolated dependency file changed while opening: "
                            f"{logical}/{relative_text}")
                    byte_count, digest, mode = _hash_stable_fd(child_fd)
                finally:
                    os.close(child_fd)
                records[relative_text] = {
                    "path": relative_text, "type": "file", "mode": mode,
                    "bytes": byte_count, "sha256": digest,
                }

        visit(descriptor, pathlib.PurePosixPath())
        for relative, record in records.items():
            if (record.get("type") == "symlink"
                    and not _dependency_symlink_resolves(records, relative)):
                raise RuntimeError(
                    f"isolated dependency symlink is dangling or cyclic: "
                    f"{logical}/{relative}")
        if _fd_identity(root_stat) != _fd_identity(os.fstat(descriptor)):
            raise RuntimeError(
                f"isolated dependency changed while scanning: {logical}")
        return records
    finally:
        os.close(descriptor)


def _hash_stable_file(path: pathlib.Path) -> tuple[int, str]:
    before = path.stat(follow_symlinks=False)
    payload = path.read_bytes()
    after = path.stat(follow_symlinks=False)
    identity = lambda item: (  # noqa: E731 - compact immutable identity
        item.st_dev, item.st_ino, item.st_mode, item.st_size,
        item.st_mtime_ns, item.st_ctime_ns,
    )
    if (identity(before) != identity(after)
            or not stat.S_ISREG(after.st_mode)
            or len(payload) != after.st_size):
        raise RuntimeError(f"isolated dependency changed while hashing: {path}")
    return len(payload), hashlib.sha256(payload).hexdigest()


def _isolated_dependency_entries() -> list[dict[str, Any]]:
    roots = _approved_dependency_roots()
    if not roots:
        raise RuntimeError("interpreter exposes no approved dependency roots")
    dist_infos: dict[str, list[pathlib.Path]] = {}
    for root in roots:
        for candidate in sorted(root.glob("*.dist-info")):
            name = _distribution_metadata_name(candidate)
            if name is not None:
                dist_infos.setdefault(name, []).append(candidate)

    selected: list[pathlib.Path] = []
    for distribution, package_names in ISOLATED_DEPENDENCY_PACKAGES.items():
        for package_name in package_names:
            matches = [root / package_name for root in roots
                       if (root / package_name).exists()]
            if len(matches) != 1:
                raise RuntimeError(
                    f"isolated dependency {package_name} has {len(matches)} "
                    "approved candidates")
            selected.append(matches[0])
        metadata_matches = dist_infos.get(
            _normalise_distribution_name(distribution), [])
        if len(metadata_matches) != 1:
            raise RuntimeError(
                f"isolated distribution {distribution} has "
                f"{len(metadata_matches)} approved metadata candidates")
        selected.append(metadata_matches[0])

    entries: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for source in sorted(selected, key=lambda value: value.name):
        if source.name in seen_names:
            raise RuntimeError(
                f"isolated dependency view has duplicate entry: {source.name}")
        seen_names.add(source.name)
        kind = "directory" if source.is_dir() else "file"
        records = list(_dependency_tree_records(source, kind).values())
        entries.append({
            "name": source.name,
            "root": str(source.parent.resolve(strict=True)),
            "kind": kind,
            "files": records,
        })
    return entries


def _isolated_dependency_manifest(
        entries: Sequence[dict[str, Any]],
        ) -> dict[str, Any]:
    payload = json.dumps(
        list(entries), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False).encode("utf-8")
    records = [
        record for entry in entries for record in entry.get("files", [])
        if isinstance(record, dict)
    ]
    return {
        "schema": "formgen-isolated-python-dependencies-v2",
        "algorithm": "sha256(canonical-json(entries))",
        "entries": len(entries),
        "files": sum(record.get("type") == "file" for record in records),
        "directories": sum(
            record.get("type") == "directory" for record in records),
        "symlinks": sum(
            record.get("type") == "symlink" for record in records),
        "bytes": sum(
            int(record.get("bytes", 0)) for record in records
            if record.get("type") == "file"),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _isolated_dependency_file_projection(
        entries: Sequence[dict[str, Any]],
        ) -> dict[str, Any]:
    """Retain the member identities needed to bind audit runtime claims."""
    members = sorted(
        ({
            "path": (
                entry["name"]
                + (f"/{record['path']}" if record.get("path") else "")),
            "bytes": record["bytes"],
            "sha256": record["sha256"],
        }
         for entry in entries
         for record in entry.get("files", [])
         if isinstance(record, dict) and record.get("type") == "file"),
        key=lambda value: value["path"],
    )
    unsigned = {
        "algorithm": "sha256(canonical-json(path,bytes,sha256))",
        "files": len(members),
        "bytes": sum(member["bytes"] for member in members),
        "members": members,
    }
    return {**unsigned, "sha256": canonical_digest(members)}


def _isolated_dependency_tree_projections(
        entries: Sequence[dict[str, Any]],
        ) -> dict[str, Any]:
    """Project each copied package tree in audit.py's closure algorithm."""
    trees: dict[str, Any] = {}
    for entry in entries:
        name = entry.get("name")
        if not isinstance(name, str) or not name or name in trees:
            raise RuntimeError("isolated dependency tree name is invalid")
        closure: list[tuple[str, str, int | None, str]] = []
        for record in entry.get("files", []):
            if not isinstance(record, dict) or not record.get("path"):
                continue
            if record.get("type") == "file":
                closure.append((
                    record["path"], "file", record["bytes"],
                    record["sha256"]))
            elif record.get("type") == "symlink":
                closure.append((
                    record["path"], "symlink", None, record["target"]))
        closure.sort(key=lambda item: item[0])
        payload = json.dumps(
            closure, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        trees[name] = {
            "logical_root": name,
            "algorithm": "sha256(canonical-json(path,type,bytes,digest))",
            "files": sum(item[1] == "file" for item in closure),
            "symlinks": sum(item[1] == "symlink" for item in closure),
            "bytes": sum(
                int(item[2] or 0) for item in closure if item[1] == "file"),
            "tree_sha256": sha256_bytes(payload),
        }
    return {
        "algorithm": "per-entry-audit-tree-closure-v1",
        "trees": trees,
        "sha256": canonical_digest(trees),
    }


def _current_isolated_dependency_manifest() -> dict[str, Any]:
    return _isolated_dependency_manifest(_isolated_dependency_entries())


def _private_dependency_record(record: dict[str, Any]) -> dict[str, Any]:
    expected = dict(record)
    if record.get("type") in {"file", "directory"}:
        expected["mode"] = int(record["mode"]) & ~0o222
    return expected


def _open_directory_components(
        root_fd: int, parts: Sequence[str],
        ) -> int:
    descriptor = os.dup(root_fd)
    try:
        for part in parts:
            if part in {"", ".", ".."} or "/" in part:
                raise RuntimeError(
                    "isolated dependency path has an unsafe component")
            child = os.open(
                part, _dependency_open_flags(directory=True),
                dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _dependency_record_parts(relative: Any) -> tuple[str, ...]:
    if not isinstance(relative, str):
        raise RuntimeError("isolated dependency path is not text")
    if relative == "":
        return ()
    path = pathlib.PurePosixPath(relative)
    if path.is_absolute() or any(part in {"", ".", ".."}
                                 for part in path.parts):
        raise RuntimeError(f"isolated dependency path is unsafe: {relative}")
    if path.as_posix() != relative:
        raise RuntimeError(
            f"isolated dependency path is not canonical: {relative}")
    return path.parts


def _verified_dependency_file(
        descriptor: int, record: dict[str, Any], *, private: bool,
        ) -> None:
    byte_count, digest, mode = _hash_stable_fd(descriptor)
    expected = _private_dependency_record(record) if private else record
    if (expected.get("type") != "file"
            or expected.get("bytes") != byte_count
            or expected.get("sha256") != digest
            or expected.get("mode") != mode):
        raise RuntimeError(
            f"isolated dependency file identity changed: {record.get('path')}")


def _copy_fd_payload(source_fd: int, destination_fd: int) -> None:
    offset = 0
    while True:
        chunk = os.pread(source_fd, 1024 * 1024, offset)
        if not chunk:
            break
        view = memoryview(chunk)
        while view:
            written = os.write(destination_fd, view)
            if written <= 0:
                raise RuntimeError("isolated dependency copy made no progress")
            view = view[written:]
        offset += len(chunk)
    os.fsync(destination_fd)


def _clone_dependency_fd(
        source_fd: int, destination_parent_fd: int, name: str,
        ) -> bool:
    if sys.platform != "darwin":
        return False
    library = ctypes.CDLL(None, use_errno=True)
    clone = getattr(library, "fclonefileat", None)
    if clone is None:
        return False
    clone.argtypes = [
        ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
    clone.restype = ctypes.c_int
    # Bind the source open descriptor and forbid destination traversal.
    flags = 0x2 | 0x8 | 0x10  # NOOWNERCOPY | NOFOLLOW_ANY | RESOLVE_BENEATH
    if clone(
            source_fd, destination_parent_fd,
            os.fsencode(name), flags) == 0:
        return True
    error_number = ctypes.get_errno()
    if error_number not in {
            errno.ENOTSUP, errno.EXDEV, errno.EINVAL, errno.ENOSYS,
            errno.EPERM, errno.EACCES}:
        raise OSError(error_number, os.strerror(error_number), name)
    try:
        os.unlink(name, dir_fd=destination_parent_fd)
    except FileNotFoundError:
        pass
    return False


def _materialize_dependency_file(
        source_fd: int, destination_parent_fd: int, name: str,
        record: dict[str, Any],
        ) -> None:
    source_before = _fd_identity(os.fstat(source_fd))
    _verified_dependency_file(source_fd, record, private=False)
    cloned = _clone_dependency_fd(source_fd, destination_parent_fd, name)
    if not cloned:
        destination_fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600, dir_fd=destination_parent_fd)
        try:
            _copy_fd_payload(source_fd, destination_fd)
        finally:
            os.close(destination_fd)
    destination_fd = os.open(
        name, _dependency_open_flags(directory=False),
        dir_fd=destination_parent_fd)
    try:
        os.fchmod(destination_fd, int(record["mode"]) & ~0o222)
        _verified_dependency_file(destination_fd, record, private=True)
    finally:
        os.close(destination_fd)
    if source_before != _fd_identity(os.fstat(source_fd)):
        raise RuntimeError(
            f"isolated dependency source changed while copying: "
            f"{record.get('path')}")


def _materialize_isolated_dependencies(
        entries: Sequence[dict[str, Any]], view: pathlib.Path,
        ) -> None:
    view_fd = _open_absolute_directory(view.resolve(strict=True))
    expected_names: set[str] = set()
    try:
        for entry in entries:
            name = entry.get("name")
            root_value = entry.get("root")
            kind = entry.get("kind")
            records_value = entry.get("files")
            if (not isinstance(name, str) or not name
                    or name in expected_names or "/" in name
                    or name in {".", ".."}
                    or not isinstance(root_value, str)
                    or not pathlib.Path(root_value).is_absolute()
                    or kind not in {"file", "directory"}
                    or not isinstance(records_value, list)):
                raise RuntimeError(
                    "isolated dependency manifest is malformed")
            expected_names.add(name)
            records: dict[str, dict[str, Any]] = {}
            for record in records_value:
                if not isinstance(record, dict):
                    raise RuntimeError(
                        "isolated dependency record is malformed")
                relative = record.get("path")
                _dependency_record_parts(relative)
                if relative in records:
                    raise RuntimeError(
                        f"isolated dependency duplicates path: {name}/{relative}")
                records[relative] = record
            root_record = records.get("")
            if (not isinstance(root_record, dict)
                    or root_record.get("type") != kind):
                raise RuntimeError(
                    f"isolated dependency root record is invalid: {name}")

            approved_root_fd = _open_absolute_directory(root_value)
            source_fd = os.open(
                name, _dependency_open_flags(directory=kind == "directory"),
                dir_fd=approved_root_fd)
            try:
                if kind == "file":
                    _materialize_dependency_file(
                        source_fd, view_fd, name, root_record)
                    continue
                source_stat = os.fstat(source_fd)
                if (not stat.S_ISDIR(source_stat.st_mode)
                        or stat.S_IMODE(source_stat.st_mode)
                        != root_record.get("mode")):
                    raise RuntimeError(
                        f"isolated dependency root changed: {name}")
                os.mkdir(name, 0o700, dir_fd=view_fd)
                destination_fd = os.open(
                    name, _dependency_open_flags(directory=True),
                    dir_fd=view_fd)
                try:
                    ordered = sorted(
                        (record for relative, record in records.items()
                         if relative),
                        key=lambda record: (
                            len(_dependency_record_parts(record["path"])),
                            record.get("type") != "directory",
                            record["path"],
                        ))
                    directory_modes: list[tuple[tuple[str, ...], int]] = []
                    for record in ordered:
                        parts = _dependency_record_parts(record["path"])
                        source_parent = _open_directory_components(
                            source_fd, parts[:-1])
                        destination_parent = _open_directory_components(
                            destination_fd, parts[:-1])
                        child_name = parts[-1]
                        try:
                            record_type = record.get("type")
                            if record_type == "directory":
                                child_source = os.open(
                                    child_name,
                                    _dependency_open_flags(directory=True),
                                    dir_fd=source_parent)
                                try:
                                    mode = stat.S_IMODE(
                                        os.fstat(child_source).st_mode)
                                finally:
                                    os.close(child_source)
                                if mode != record.get("mode"):
                                    raise RuntimeError(
                                        "isolated dependency directory mode "
                                        f"changed: {name}/{record['path']}")
                                os.mkdir(
                                    child_name, 0o700,
                                    dir_fd=destination_parent)
                                directory_modes.append((parts, mode))
                            elif record_type == "file":
                                child_source = os.open(
                                    child_name,
                                    _dependency_open_flags(directory=False),
                                    dir_fd=source_parent)
                                try:
                                    _materialize_dependency_file(
                                        child_source, destination_parent,
                                        child_name, record)
                                finally:
                                    os.close(child_source)
                            elif record_type == "symlink":
                                before = os.stat(
                                    child_name, dir_fd=source_parent,
                                    follow_symlinks=False)
                                target = os.readlink(
                                    child_name, dir_fd=source_parent)
                                after = os.stat(
                                    child_name, dir_fd=source_parent,
                                    follow_symlinks=False)
                                if (_fd_identity(before) != _fd_identity(after)
                                        or not stat.S_ISLNK(before.st_mode)
                                        or target != record.get("target")
                                        or not _symlink_target_is_internal(
                                            record["path"], target)):
                                    raise RuntimeError(
                                        "isolated dependency symlink changed: "
                                        f"{name}/{record['path']}")
                                os.symlink(
                                    target, child_name,
                                    dir_fd=destination_parent)
                            else:
                                raise RuntimeError(
                                    "isolated dependency record type is "
                                    f"invalid: {name}/{record['path']}")
                        finally:
                            os.close(source_parent)
                            os.close(destination_parent)
                    for parts, mode in sorted(
                            directory_modes,
                            key=lambda item: len(item[0]), reverse=True):
                        child_destination = _open_directory_components(
                            destination_fd, parts)
                        try:
                            os.fchmod(child_destination, mode & ~0o222)
                        finally:
                            os.close(child_destination)
                    os.fchmod(
                        destination_fd,
                        int(root_record["mode"]) & ~0o222)
                finally:
                    os.close(destination_fd)
                if _fd_identity(source_stat) != _fd_identity(
                        os.fstat(source_fd)):
                    raise RuntimeError(
                        f"isolated dependency root changed while copying: {name}")
            finally:
                os.close(source_fd)
                os.close(approved_root_fd)
    finally:
        os.close(view_fd)
    errors = _validate_isolated_dependencies(entries, view)
    if errors:
        raise RuntimeError("; ".join(errors))
    view.chmod(stat.S_IMODE(view.stat().st_mode) & ~0o222)


def _validate_isolated_dependencies(
        entries: Sequence[dict[str, Any]], view: pathlib.Path,
        ) -> list[str]:
    errors: list[str] = []
    expected_names: set[str] = set()
    for entry in entries:
        name = entry.get("name")
        root_value = entry.get("root")
        kind = entry.get("kind")
        records = entry.get("files")
        if (not isinstance(name, str) or not name or name in expected_names
                or not isinstance(root_value, str)
                or kind not in {"file", "directory"}
                or not isinstance(records, list)):
            errors.append("isolated dependency manifest is malformed")
            continue
        expected_names.add(name)
        expected_files = {
            record.get("path"): _private_dependency_record(record)
            for record in records
            if isinstance(record, dict) and isinstance(record.get("path"), str)
        }
        if len(expected_files) != len(records):
            errors.append(f"isolated dependency manifest duplicates paths: {name}")
            continue
        try:
            actual_files = _dependency_tree_records(view / name, kind)
        except (OSError, RuntimeError) as error:
            errors.append(
                f"isolated private dependency cannot be read: {name}: {error}")
            continue
        if actual_files != expected_files:
            errors.append(
                f"isolated private dependency inventory/digest changed: {name}")
    try:
        actual_names = {path.name for path in view.iterdir()}
    except OSError as error:
        errors.append(f"isolated dependency view cannot be read: {error}")
    else:
        if actual_names != expected_names:
            errors.append("isolated dependency view inventory changed")
    return errors


ISOLATED_PYTHON_BOOTSTRAP = r'''#!/usr/bin/env python3
import hashlib
import json
import os
import pathlib
import runpy
import signal
import stat
import subprocess
import sys
import time


def fail(message):
    raise RuntimeError("isolated Python bootstrap: " + message)


def stable_payload(path):
    before = path.stat(follow_symlinks=False)
    payload = path.read_bytes()
    after = path.stat(follow_symlinks=False)
    identity = lambda item: (
        item.st_dev, item.st_ino, item.st_mode, item.st_size,
        item.st_mtime_ns, item.st_ctime_ns)
    if (identity(before) != identity(after)
            or not stat.S_ISREG(after.st_mode)
            or len(payload) != after.st_size):
        fail("file changed while hashing: " + str(path))
    return payload


def tree_records(source, kind):
    if source.is_symlink():
        fail("dependency is a symlink: " + str(source))
    if kind == "file":
        if not source.is_file():
            fail("dependency file is absent: " + str(source))
        payload = stable_payload(source)
        return {"": {
            "path": "", "type": "file", "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "mode": stat.S_IMODE(
                source.stat(follow_symlinks=False).st_mode)}}
    if kind != "directory" or not source.is_dir():
        fail("dependency directory is absent: " + str(source))
    root = source.resolve(strict=True)
    records = {"": {
        "path": "", "type": "directory",
        "mode": stat.S_IMODE(source.stat(follow_symlinks=False).st_mode)}}
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            target = os.readlink(path)
            try:
                path.resolve(strict=True).relative_to(root)
            except (FileNotFoundError, ValueError):
                fail("dependency symlink escapes: " + str(path))
            records[relative.as_posix()] = {
                "path": relative.as_posix(), "type": "symlink",
                "target": target}
            continue
        if path.is_dir():
            records[relative.as_posix()] = {
                "path": relative.as_posix(), "type": "directory",
                "mode": stat.S_IMODE(
                    path.stat(follow_symlinks=False).st_mode)}
            continue
        if not stat.S_ISREG(path.stat(follow_symlinks=False).st_mode):
            fail("dependency is not regular: " + str(path))
        payload = stable_payload(path)
        records[relative.as_posix()] = {
            "path": relative.as_posix(), "type": "file",
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "mode": stat.S_IMODE(
                path.stat(follow_symlinks=False).st_mode)}
    return records


def validate_dependencies(spec):
    view = pathlib.Path(spec["dependency_view"])
    expected_names = set()
    for entry in spec["dependencies"]:
        name = entry["name"]
        if name in expected_names:
            fail("duplicate dependency entry: " + name)
        expected_names.add(name)
        source = view / name
        actual = tree_records(source, entry["kind"])
        expected = {}
        for record in entry["files"]:
            private = dict(record)
            if private.get("type") in {"file", "directory"}:
                private["mode"] = int(private["mode"]) & ~0o222
            expected[private["path"]] = private
        if len(expected) != len(entry["files"]) or actual != expected:
            fail("private dependency inventory/digest changed: " + name)
    if {path.name for path in view.iterdir()} != expected_names:
        fail("dependency view inventory changed")


def load_bound_json(path, digest):
    payload = stable_payload(path)
    if hashlib.sha256(payload).hexdigest() != digest:
        fail("launch specification digest changed")
    value = json.loads(payload)
    if not isinstance(value, dict) or value.get("schema") != 1:
        fail("launch specification is malformed")
    return value


def write_root_receipt(
        spec, target_argv, worker_exit, target_exit,
        lingering_descendants_detected, cleanup_complete):
    value = {
        "schema": "formgen-isolated-python-bootstrap-receipt-v2",
        "executable": str(pathlib.Path(sys.executable).resolve()),
        "isolated": sys.flags.isolated,
        "no_site": sys.flags.no_site,
        "dont_write_bytecode": bool(sys.dont_write_bytecode),
        "pycache_prefix": str(pathlib.Path(sys.pycache_prefix or "").resolve()),
        "cwd": str(pathlib.Path.cwd().resolve()),
        "pythonpath_absent": "PYTHONPATH" not in os.environ,
        "pythonhome_absent": "PYTHONHOME" not in os.environ,
        "site_not_loaded": "site" not in sys.modules,
        "bootstrap_sha256": spec["bootstrap_sha256"],
        "spec_sha256": spec["spec_sha256"],
        "dependency_manifest_sha256": spec["dependency_manifest"]["sha256"],
        "target_argv_sha256": spec["target_argv_sha256"],
        "worker_exit": worker_exit,
        "target_exit": target_exit,
        "recursive_launcher_installed": True,
        "process_group_supervised": True,
        "subprocess_popen_python_rewrite_installed": True,
        "os_process_control_guards_installed": True,
        "lingering_descendants_detected": lingering_descendants_detected,
        "cleanup_complete": cleanup_complete,
    }
    payload = (json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n").encode("utf-8")
    temporary = pathlib.Path(spec["receipt_path"] + "." + str(os.getpid()))
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        if os.write(descriptor, payload) != len(payload):
            fail("root receipt write was partial")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, spec["receipt_path"])


def isolated_command(spec, python_argv):
    return [
        sys.executable, "-I", "-S", "-B", "-X",
        "pycache_prefix=" + spec["pycache_prefix"],
        spec["bootstrap"], spec["spec_path"], spec["spec_sha256"],
        spec["bootstrap_sha256"], "child", str(os.getpgrp()), "--",
        *python_argv,
    ]


def install_recursive_launcher(spec):
    original_popen = subprocess.Popen
    executable = pathlib.Path(spec["executable"])
    bootstrap = pathlib.Path(spec["bootstrap"])

    def resolved_command(value, environment):
        try:
            raw = os.fsdecode(os.fspath(value))
        except (OSError, TypeError, ValueError):
            return None
        if os.path.sep in raw:
            candidates = [pathlib.Path(raw)]
        else:
            candidates = [
                pathlib.Path(directory) / raw
                for directory in os.get_exec_path(environment)]
        for candidate in candidates:
            try:
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    return candidate.resolve()
            except (OSError, RuntimeError):
                continue
        return None

    def bounded_popen(command, *args, **kwargs):
        if args:
            fail("positional subprocess options are forbidden")
        if kwargs.get("preexec_fn") is not None:
            fail("subprocess preexec_fn can escape process supervision")
        if kwargs.get("executable") is not None:
            fail("subprocess executable override is forbidden")
        if kwargs.get("shell"):
            fail("subprocess shell mode is forbidden")
        kwargs["close_fds"] = True
        kwargs["start_new_session"] = False
        if "process_group" in kwargs:
            kwargs["process_group"] = None
        rewritten = command
        is_python = False
        if (not kwargs.get("shell") and isinstance(command, (list, tuple))
                and command):
            effective_environment = dict(
                os.environ if kwargs.get("env") is None else kwargs["env"])
            is_python = (
                resolved_command(command[0], effective_environment)
                == executable)
            if is_python:
                rewritten = isolated_command(spec, list(command[1:]))
                environment = effective_environment
                environment.pop("PYTHONPATH", None)
                environment.pop("PYTHONHOME", None)
                environment["PYTHONDONTWRITEBYTECODE"] = "1"
                environment["PYTHONPYCACHEPREFIX"] = spec["pycache_prefix"]
                environment["PYTHONNOUSERSITE"] = "1"
                environment["PYTHONSAFEPATH"] = "1"
                kwargs["env"] = environment
        process = original_popen(rewritten, *args, **kwargs)
        if os.name == "posix":
            observed_session = os.getsid(process.pid)
            observed_group = os.getpgid(process.pid)
        else:
            observed_session = observed_group = -1
        if (observed_session != os.getsid(0)
                or observed_group != os.getpgrp()):
            try:
                if (observed_group > 1
                        and observed_group != os.getpgrp()):
                    os.killpg(observed_group, signal.SIGKILL)
                else:
                    process.kill()
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass
            fail("subprocess escaped the supervised process group")
        return process

    subprocess.Popen = bounded_popen
    # On Linux CPython's Popen calls os.posix_spawn INTERNALLY when it can;
    # macOS always takes the fork_exec path. Blocking os.posix_spawn below
    # therefore made every supervised Popen self-destruct on Linux only --
    # the first real runner execution died inside its own probes with
    # "process detachment is forbidden". Forcing the fork path makes both
    # platforms behave identically, while a DIRECT os.posix_spawn call (the
    # actual escape hatch being guarded) stays blocked.
    subprocess._USE_POSIX_SPAWN = False
    if os.name == "posix":
        def refuse_detachment(*_args, **_kwargs):
            fail("process detachment is forbidden in isolated execution")

        for name in (
                "_exit", "fork", "forkpty", "posix_spawn", "posix_spawnp",
                "execl", "execle", "execlp", "execlpe",
                "execv", "execve", "execvp", "execvpe",
                "setsid", "setpgrp", "setpgid", "system", "popen",
                "spawnl", "spawnle", "spawnlp", "spawnlpe",
                "spawnv", "spawnve", "spawnvp", "spawnvpe"):
            if hasattr(os, name):
                setattr(os, name, refuse_detachment)
    return original_popen


def process_group_members(group):
    if os.name != "posix":
        fail("process-group supervision requires POSIX")
    executable = next(
        (path for path in ("/bin/ps", "/usr/bin/ps")
         if pathlib.Path(path).is_file()), None)
    if executable is None:
        fail("cannot locate an absolute ps executable")
    probe = subprocess.Popen(
        [executable, "-axo", "pid=,pgid="],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=False)
    output, diagnostic = probe.communicate(timeout=5)
    if probe.returncode != 0:
        fail("cannot enumerate supervised process group: " + diagnostic)
    members = set()
    for line in output.splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        try:
            pid, pgid = map(int, fields)
        except ValueError:
            continue
        if pgid == group and pid != probe.pid:
            members.add(pid)
    return members


def clean_supervised_process_group():
    group = os.getpgrp()
    if os.getpid() != group or os.getsid(0) != group:
        fail("root launcher is not the live process-group/session leader")
    lingering = process_group_members(group) - {os.getpid()}
    detected = bool(lingering)
    deadline = time.monotonic() + 5.0
    while lingering and time.monotonic() < deadline:
        for pid in sorted(lingering):
            try:
                if os.getpgid(pid) == group:
                    os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        time.sleep(0.05)
        lingering = process_group_members(group) - {os.getpid()}
    return detected, not lingering


def supervise_root(spec, target_argv):
    original_popen = install_recursive_launcher(spec)
    worker = original_popen(
        isolated_command(spec, target_argv),
        close_fds=True, start_new_session=False)
    worker_exit = worker.wait()
    validate_dependencies(load_bound_json(
        pathlib.Path(spec["spec_path"]), spec["spec_sha256"]))
    lingering, cleanup_complete = clean_supervised_process_group()
    target_exit = worker_exit
    if lingering or not cleanup_complete:
        target_exit = 125
    write_root_receipt(
        spec, target_argv, worker_exit, target_exit,
        lingering, cleanup_complete)
    raise SystemExit(target_exit)


def dispatch(argv):
    values = list(argv)
    while values and values[0] in {"-B", "-E", "-I", "-S", "-s", "-P", "-u"}:
        values.pop(0)
    while values and (values[0] == "-X" or values[0].startswith("-X")):
        option = values.pop(0)
        if option == "-X":
            if not values:
                fail("missing value for -X")
            values.pop(0)
    if not values:
        fail("no Python target was supplied")
    if values[0] == "-c":
        if len(values) < 2:
            fail("missing source for -c")
        source = values[1]
        sys.argv = ["-c", *values[2:]]
        namespace = {
            "__name__": "__main__", "__package__": None,
            "__spec__": None, "__builtins__": __builtins__,
        }
        exec(compile(source, "<string>", "exec"), namespace, namespace)
        return
    if values[0] == "-m":
        if len(values) < 2:
            fail("missing module for -m")
        sys.argv = [values[1], *values[2:]]
        sys.path.insert(0, os.getcwd())
        runpy.run_module(values[1], run_name="__main__", alter_sys=True)
        return
    if values[0].startswith("-"):
        fail("unsupported Python option: " + values[0])
    target = pathlib.Path(values[0]).resolve(strict=True)
    if not target.is_file():
        fail("Python target is not a file: " + str(target))
    sys.argv = [str(target), *values[1:]]
    sys.path.insert(0, str(target.parent))
    runpy.run_path(str(target), run_name="__main__")


def main():
    if (len(sys.argv) < 7 or sys.argv[6] != "--"
            or sys.argv[4] not in {"root", "child"}):
        fail("bootstrap argv is malformed")
    spec_path = pathlib.Path(sys.argv[1])
    spec_digest = sys.argv[2]
    bootstrap_digest = sys.argv[3]
    mode = sys.argv[4]
    group_token = sys.argv[5]
    target_argv = sys.argv[7:]
    bootstrap = pathlib.Path(__file__).resolve(strict=True)
    if hashlib.sha256(stable_payload(bootstrap)).hexdigest() != bootstrap_digest:
        fail("bootstrap digest changed")
    spec = load_bound_json(spec_path, spec_digest)
    if spec.get("spec_sha256") != "":
        fail("launch specification digest slot is not canonical")
    spec["spec_sha256"] = spec_digest
    if (pathlib.Path(sys.executable).resolve() != pathlib.Path(spec["executable"])
            or pathlib.Path.cwd().resolve() != pathlib.Path(spec["repo"])
            or pathlib.Path(sys.pycache_prefix or "").resolve()
            != pathlib.Path(spec["pycache_prefix"]).resolve()
            or not sys.flags.isolated or not sys.flags.no_site
            or not sys.dont_write_bytecode or "site" in sys.modules):
        fail("interpreter isolation contract is false")
    if (str(bootstrap) != spec["bootstrap"]
            or str(spec_path.resolve(strict=True)) != spec["spec_path"]
            or bootstrap_digest != spec["bootstrap_sha256"]):
        fail("launch identity is false")
    validate_dependencies(spec)
    sys.path.append(spec["dependency_view"])
    if mode == "root":
        if (group_token != "root" or os.name != "posix"
                or os.getpid() != os.getpgrp()
                or os.getpid() != os.getsid(0)):
            fail("root launcher is not its own session/process-group leader")
        supervise_root(spec, target_argv)
    try:
        expected_group = int(group_token)
    except ValueError:
        fail("worker process-group identity is malformed")
    if (os.name != "posix" or expected_group <= 1
            or os.getpgrp() != expected_group
            or os.getsid(0) != expected_group):
        fail("worker escaped the root launcher's supervised process group")
    install_recursive_launcher(spec)
    pending = None
    target_exit = 0
    try:
        dispatch(target_argv)
    except SystemExit as error:
        pending = error
        target_exit = error.code if isinstance(error.code, int) else (
            0 if error.code is None else 1)
    except BaseException as error:
        pending = error
        target_exit = 1
    try:
        validate_dependencies(load_bound_json(spec_path, spec_digest))
        if hashlib.sha256(stable_payload(bootstrap)).hexdigest() != bootstrap_digest:
            fail("bootstrap changed during execution")
    except BaseException as error:
        pending = error
        target_exit = 1
    if pending is not None:
        raise pending


main()
'''


@dataclasses.dataclass
class IsolatedPythonExecution:
    code: int
    output: str
    receipt: dict[str, Any] | None


def _compact_digest(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _host_process_group_members(group: int) -> tuple[set[int], list[str]]:
    errors: list[str] = []
    executable = next(
        (path for path in ("/bin/ps", "/usr/bin/ps")
         if pathlib.Path(path).is_file()), None)
    if executable is None:
        return set(), ["cannot locate an absolute ps executable"]
    try:
        result = subprocess.run(
            [executable, "-axo", "pid=,pgid="],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            timeout=2, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return set(), [
            f"cannot enumerate isolated process group: {error}"]
    if result.returncode != 0:
        return set(), [
            "cannot enumerate isolated process group: "
            + result.stderr.strip()]
    members: set[int] = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        try:
            pid, process_group = map(int, fields)
        except ValueError:
            continue
        if process_group == group:
            members.add(pid)
    return members, errors


def _signal_isolated_group_members(
        group: int, members: Iterable[int],
        ) -> list[str]:
    errors: list[str] = []
    for pid in sorted(set(members), reverse=True):
        if pid <= 1 or pid == os.getpid():
            errors.append(f"refused unsafe isolated process id: {pid}")
            continue
        try:
            if os.getpgid(pid) != group:
                errors.append(
                    f"isolated process left its bound group: {pid}")
                continue
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError as error:
            errors.append(
                f"cannot signal isolated process {pid}: {error}")
    return errors


def _force_reap_isolated_process(
        process: subprocess.Popen[Any], group: int | None,
        ) -> tuple[bool, list[str]]:
    """Best-effort bounded finalizer that never trusts a recycled root PID."""
    errors: list[str] = []
    deadline = time.monotonic() + COMB_REFEREE_CLEANUP_TIMEOUT_SECONDS
    if group is None:
        try:
            process.kill()
        except (OSError, ProcessLookupError) as error:
            errors.append(f"cannot kill unbound isolated root: {error}")
        try:
            process.wait(timeout=max(0.1, deadline - time.monotonic()))
        except (OSError, subprocess.TimeoutExpired) as error:
            errors.append(f"cannot reap unbound isolated root: {error}")
            return False, errors
        return True, errors

    root_exited = False
    group_quiescent = False
    while time.monotonic() < deadline:
        if not root_exited:
            try:
                root_exited = os.waitid(
                    os.P_PID, process.pid,
                    os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
            except ChildProcessError:
                errors.append(
                    "isolated root was reaped before final cleanup")
                break
            except OSError as error:
                errors.append(
                    f"cannot observe isolated root during cleanup: {error}")
        members, member_errors = _host_process_group_members(group)
        errors.extend(error for error in member_errors if error not in errors)
        allowed = {process.pid} if root_exited else set()
        residual = members - allowed
        if not member_errors and not residual and root_exited:
            group_quiescent = True
            break
        targets = set(residual)
        if not root_exited:
            targets.add(process.pid)
        for error in _signal_isolated_group_members(group, targets):
            if error not in errors:
                errors.append(error)
        time.sleep(0.05)
    try:
        process.wait(timeout=max(0.1, deadline - time.monotonic()))
    except (OSError, subprocess.TimeoutExpired) as error:
        errors.append(f"cannot reap isolated root after cleanup: {error}")
        return False, errors
    return group_quiescent, errors


def _wait_and_reap_isolated_process(
        process: subprocess.Popen[Any], timeout: int,
        ) -> tuple[bool, bool, list[str]]:
    errors: list[str] = []
    group: int | None = None
    reaped = False
    pending: BaseException | None = None
    timed_out = False
    cleanup_complete = False
    lingering: set[int] = set()
    try:
        if (os.name != "posix" or not hasattr(os, "waitid")
                or not hasattr(os, "WNOWAIT")):
            raise RuntimeError(
                "isolated process supervision requires POSIX waitid/WNOWAIT")
        observed_group = os.getpgid(process.pid)
        if (observed_group != process.pid or observed_group <= 1
                or observed_group == os.getpgrp()):
            raise RuntimeError(
                f"refused unsafe isolated process group: {observed_group}")
        group = observed_group
        deadline = time.monotonic() + timeout
        exited = False
        while time.monotonic() < deadline:
            try:
                observation = os.waitid(
                    os.P_PID, process.pid,
                    os.WEXITED | os.WNOHANG | os.WNOWAIT)
            except ChildProcessError:
                raise RuntimeError(
                    "isolated process was reaped outside its supervisor")
            if observation is not None:
                exited = True
                break
            time.sleep(0.05)
        timed_out = not exited
        members, member_errors = _host_process_group_members(group)
        errors.extend(error for error in member_errors if error not in errors)
        lingering = members - {process.pid} if exited else set()
        if timed_out or lingering or member_errors:
            finalized, final_errors = _force_reap_isolated_process(
                process, group)
            reaped = True
            errors.extend(error for error in final_errors if error not in errors)
            cleanup_complete = finalized and not member_errors
        else:
            cleanup_deadline = (
                time.monotonic() + COMB_REFEREE_CLEANUP_TIMEOUT_SECONDS)
            process.wait(timeout=max(
                0.1, cleanup_deadline - time.monotonic()))
            reaped = True
            cleanup_complete = True
    except BaseException as error:  # cleanup happens in the finalizer below
        pending = error
    finally:
        if not reaped:
            finalized, final_errors = _force_reap_isolated_process(
                process, group)
            reaped = True
            cleanup_complete = cleanup_complete and finalized
            errors.extend(error for error in final_errors if error not in errors)
    if pending is not None:
        if isinstance(pending, (KeyboardInterrupt, SystemExit)):
            raise pending
        errors.append(
            "isolated process supervisor raised: "
            f"{type(pending).__name__}: {pending}")
        cleanup_complete = False
    if lingering:
        errors.append(
            f"{len(lingering)} supervised descendant(s) outlived the root")
        cleanup_complete = False
    return timed_out, cleanup_complete, errors


def _validate_bootstrap_receipt(
        value: Any, spec: dict[str, Any], child_exit: int,
        ) -> list[str]:
    expected_keys = {
        "schema", "executable", "isolated", "no_site",
        "dont_write_bytecode", "pycache_prefix", "cwd",
        "pythonpath_absent", "pythonhome_absent", "site_not_loaded",
        "bootstrap_sha256", "spec_sha256", "dependency_manifest_sha256",
        "target_argv_sha256", "worker_exit", "target_exit",
        "recursive_launcher_installed", "process_group_supervised",
        "subprocess_popen_python_rewrite_installed",
        "os_process_control_guards_installed",
        "lingering_descendants_detected",
        "cleanup_complete",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        return ["isolated bootstrap receipt schema is invalid"]
    expected = {
        "schema": "formgen-isolated-python-bootstrap-receipt-v2",
        "executable": spec["executable"],
        "isolated": 1,
        "no_site": 1,
        "dont_write_bytecode": True,
        "pycache_prefix": spec["pycache_prefix"],
        "cwd": spec["repo"],
        "pythonpath_absent": True,
        "pythonhome_absent": True,
        "site_not_loaded": True,
        "bootstrap_sha256": spec["bootstrap_sha256"],
        "spec_sha256": spec["spec_sha256"],
        "dependency_manifest_sha256": spec["dependency_manifest"]["sha256"],
        "target_argv_sha256": spec["target_argv_sha256"],
        "worker_exit": child_exit,
        "target_exit": child_exit,
        "recursive_launcher_installed": True,
        "process_group_supervised": True,
        "subprocess_popen_python_rewrite_installed": True,
        "os_process_control_guards_installed": True,
        "lingering_descendants_detected": False,
        "cleanup_complete": True,
    }
    return [] if value == expected else ["isolated bootstrap receipt is false"]


def run_isolated_python_attested(
        args: list[str], timeout: int = 5400,
        base_environment: dict[str, str] | None = None,
        ) -> IsolatedPythonExecution:
    environment = dict(
        os.environ if base_environment is None else base_environment)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    try:
        with tempfile.TemporaryDirectory(
                prefix=".gate-python-isolated-") as temporary:
            root = pathlib.Path(temporary)
            pycache_prefix = root / "pycache"
            dependency_view = root / "dependencies"
            pycache_prefix.mkdir()
            dependency_view.mkdir()
            entries = _isolated_dependency_entries()
            _materialize_isolated_dependencies(entries, dependency_view)
            dependency_errors = _validate_isolated_dependencies(
                entries, dependency_view)
            if dependency_errors:
                return IsolatedPythonExecution(
                    125, "; ".join(dependency_errors) + "\n", None)
            bootstrap = root / "bootstrap.py"
            bootstrap.write_text(ISOLATED_PYTHON_BOOTSTRAP, encoding="utf-8")
            bootstrap_payload = bootstrap.read_bytes()
            bootstrap_digest = hashlib.sha256(bootstrap_payload).hexdigest()
            spec_path = root / "launch.json"
            receipt_path = root / "receipt.json"
            dependency_manifest = _isolated_dependency_manifest(entries)
            spec: dict[str, Any] = {
                "schema": 1,
                "executable": str(pathlib.Path(sys.executable).resolve()),
                "repo": str(REPO.resolve()),
                "pycache_prefix": str(pycache_prefix.resolve()),
                "dependency_view": str(dependency_view.resolve()),
                "dependencies": entries,
                "dependency_manifest": dependency_manifest,
                "bootstrap": str(bootstrap.resolve()),
                "bootstrap_sha256": bootstrap_digest,
                "spec_path": str(spec_path.resolve()),
                "spec_sha256": "",
                "receipt_path": str(receipt_path.resolve()),
                "target_argv_sha256": _compact_digest(args),
            }
            # The digest field cannot include itself.  Bind the canonical
            # unsigned payload and publish that digest beside the file.
            unsigned_payload = json.dumps(
                spec, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False).encode("utf-8")
            spec_digest = hashlib.sha256(unsigned_payload).hexdigest()
            spec_path.write_bytes(unsigned_payload)
            spec["spec_sha256"] = spec_digest
            # The bootstrap receives the digest out of band and confirms the
            # on-disk bytes.  Its in-memory spec records the same value only
            # after loading so recursive children can reuse it.
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            environment["PYTHONPYCACHEPREFIX"] = str(pycache_prefix)
            environment["PYTHONNOUSERSITE"] = "1"
            environment["PYTHONSAFEPATH"] = "1"
            command = [
                sys.executable, "-I", "-S", "-B", "-X",
                f"pycache_prefix={pycache_prefix}", str(bootstrap),
                str(spec_path), spec_digest, bootstrap_digest,
                "root", "root", "--", *args,
            ]
            stdout_path = root / "stdout.log"
            stderr_path = root / "stderr.log"
            with (stdout_path.open("wb") as stdout_stream,
                  stderr_path.open("wb") as stderr_stream):
                process = subprocess.Popen(
                    command, cwd=REPO,
                    stdout=stdout_stream, stderr=stderr_stream,
                    env=environment, start_new_session=(os.name == "posix"))
                timed_out, cleanup_complete, cleanup_errors = (
                    _wait_and_reap_isolated_process(process, timeout))
            stdout = stdout_path.read_text(
                encoding="utf-8", errors="replace")
            stderr = stderr_path.read_text(
                encoding="utf-8", errors="replace")
            if timed_out:
                detail = stdout + stderr
                if cleanup_errors:
                    detail += "\n" + "; ".join(cleanup_errors)
                detail += f"\nisolated Python process exceeded {timeout}s\n"
                receipt = {
                    "schema": "formgen-isolated-python-launch-receipt-v2",
                    "timed_out": True,
                    "cleanup_complete": cleanup_complete,
                    "dependency_manifest": dependency_manifest,
                    "command_flags": list(ISOLATED_PYTHON_ATTESTED_FLAGS),
                    "process_group_supervised": True,
                    "subprocess_popen_python_rewrite_installed": True,
                    "os_process_control_guards_installed": True,
                }
                return IsolatedPythonExecution(124, detail, receipt)
            if not cleanup_complete or cleanup_errors:
                detail = stdout + stderr
                if cleanup_errors:
                    detail += "\n" + "; ".join(cleanup_errors)
                return IsolatedPythonExecution(125, detail + "\n", None)
            post_errors = _validate_isolated_dependencies(
                entries, dependency_view)
            if (bootstrap.read_bytes() != bootstrap_payload
                    or hashlib.sha256(spec_path.read_bytes()).hexdigest()
                    != spec_digest):
                post_errors.append("isolated launcher bytes changed")
            if post_errors:
                return IsolatedPythonExecution(
                    125, stdout + stderr + "\n"
                    + "; ".join(post_errors) + "\n", None)
            try:
                bootstrap_receipt = json.loads(receipt_path.read_bytes())
            except (OSError, UnicodeError, ValueError, RecursionError) as error:
                return IsolatedPythonExecution(
                    125, stdout + stderr
                    + f"\nisolated bootstrap receipt is absent: {error}\n",
                    None)
            receipt_errors = _validate_bootstrap_receipt(
                bootstrap_receipt, spec, process.returncode)
            if receipt_errors:
                return IsolatedPythonExecution(
                    125, stdout + stderr + "\n"
                    + "; ".join(receipt_errors) + "\n", None)
            receipt = {
                "schema": "formgen-isolated-python-launch-receipt-v2",
                "bootstrap": bootstrap_receipt,
                "dependency_manifest": dependency_manifest,
                "command_flags": list(ISOLATED_PYTHON_ATTESTED_FLAGS),
                "pythonpath_removed": True,
                "pythonhome_removed": True,
                "source_dependencies_copied_from_verified_fds": True,
                "private_dependencies_validated_before_after": True,
                "process_group_supervised": True,
                "subprocess_popen_python_rewrite_installed": True,
                "os_process_control_guards_installed": True,
                "supervised_group_quiescent": True,
                "timed_out": False,
                "cleanup_complete": True,
                "child_exit": process.returncode,
            }
            return IsolatedPythonExecution(
                process.returncode, stdout + stderr, receipt)
    except Exception as error:  # noqa: BLE001 - isolation failure is evidence
        return IsolatedPythonExecution(
            125, "isolated Python launcher could not be established: "
            f"{type(error).__name__}: {error}\n", None)


def run_isolated_python(
        args: list[str], timeout: int = 5400,
        base_environment: dict[str, str] | None = None,
        ) -> tuple[int, str]:
    execution = run_isolated_python_attested(
        args, timeout, base_environment)
    return execution.code, execution.output


def _run_direct_supervised_python(
        args: list[str], timeout: int, *, no_site: bool,
        ) -> tuple[int, str]:
    """Run a behavioral self-test outside the production bootstrap.

    Gate/referee must exercise their own supervisor; audit's short browser
    deadline must avoid paying for a second nested dependency materialization.
    Gate/referee remain isolated and no-site. Audit uses ``-E`` so its approved
    user-site dependencies stay available; it is still sanitized,
    bytecode-free, and supervised. This is behavioral coverage, not evidence.
    """
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    try:
        with tempfile.TemporaryDirectory(
                prefix=".gate-self-supervising-") as temporary:
            root = pathlib.Path(temporary)
            pycache_prefix = root / "pycache"
            pycache_prefix.mkdir()
            stdout_path = root / "stdout.log"
            stderr_path = root / "stderr.log"
            environment.update({
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPYCACHEPREFIX": str(pycache_prefix),
                "PYTHONNOUSERSITE": "1",
                "PYTHONSAFEPATH": "1",
            })
            python_flags = (["-I", "-S", "-B"] if no_site
                            else ["-E", "-B"])
            command = [sys.executable, *python_flags, "-X",
                       f"pycache_prefix={pycache_prefix}", *args]
            with (stdout_path.open("wb") as stdout_stream,
                  stderr_path.open("wb") as stderr_stream):
                process = subprocess.Popen(
                    command, cwd=REPO, stdout=stdout_stream,
                    stderr=stderr_stream, env=environment,
                    start_new_session=(os.name == "posix"))
                timed_out, cleanup_complete, cleanup_errors = (
                    _wait_and_reap_isolated_process(process, timeout))
            output = stdout_path.read_text(
                encoding="utf-8", errors="replace")
            output += stderr_path.read_text(
                encoding="utf-8", errors="replace")
            if timed_out:
                return 124, output + (
                    f"\nself-supervising Python exceeded {timeout}s\n")
            if not cleanup_complete or cleanup_errors:
                return 125, output + "\n" + "; ".join(cleanup_errors)
            return int(process.returncode), output
    except Exception as error:  # noqa: BLE001 - self-test must fail closed
        return 125, (
            "self-supervising Python could not be established: "
            f"{type(error).__name__}: {error}\n")


def run_self_supervising_python(
        args: list[str], timeout: int = 900,
        ) -> tuple[int, str]:
    return _run_direct_supervised_python(args, timeout, no_site=True)


def run_dependency_self_test_python(
        args: list[str], timeout: int = 900,
        ) -> tuple[int, str]:
    return _run_direct_supervised_python(args, timeout, no_site=False)


def load(path: pathlib.Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - absent or unreadable is "cannot evaluate"
        return None


class CombRefereeScopeError(RuntimeError):
    """The application-scoped referee claim cannot be evaluated safely."""


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    return sha256_bytes(payload)


def _is_sha256(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(character in "0123456789abcdef" for character in value))


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _json_type_exact_equal(left: Any, right: Any) -> bool:
    """Compare JSON-shaped evidence without Python's bool/int equivalence."""
    pending = [(left, right)]
    while pending:
        observed, expected = pending.pop()
        if type(observed) is not type(expected):
            return False
        if isinstance(observed, dict):
            if set(observed) != set(expected):
                return False
            pending.extend(
                (observed[key], expected[key]) for key in observed)
        elif isinstance(observed, list):
            if len(observed) != len(expected):
                return False
            pending.extend(zip(observed, expected))
        elif observed != expected:
            return False
    return True


def _stable_file_record(path: pathlib.Path, logical: str) -> dict[str, Any]:
    """Read one regular file and reject mutation during the read itself."""
    try:
        if path.is_symlink():
            raise CombRefereeScopeError(f"symlink is outside the scope: {logical}")
        before = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise CombRefereeScopeError(f"not a regular file: {logical}")
        payload = path.read_bytes()
        after = path.stat(follow_symlinks=False)
    except OSError as error:
        raise CombRefereeScopeError(f"cannot read {logical}: {error}") from error
    identity = lambda value: (  # noqa: E731 - compact immutable stat identity
        value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
        value.st_ctime_ns,
    )
    if identity(before) != identity(after) or len(payload) != after.st_size:
        raise CombRefereeScopeError(f"file changed while hashing: {logical}")
    return {"path": logical, "bytes": len(payload),
            "sha256": sha256_bytes(payload)}


def _file_manifest(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(records, key=lambda record: str(record["path"]))
    if len({str(record["path"]) for record in ordered}) != len(ordered):
        raise CombRefereeScopeError("snapshot contains duplicate logical paths")
    return {
        "file_count": len(ordered),
        "bytes": sum(int(record["bytes"]) for record in ordered),
        "sha256": canonical_digest(ordered),
        "files": ordered,
    }


def _tree_manifest(root: pathlib.Path, logical_root: str) -> dict[str, Any]:
    if root.is_symlink() or not root.is_dir():
        raise CombRefereeScopeError(f"missing or unsafe tree: {logical_root}")
    records: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        logical = f"{logical_root}/{path.relative_to(root).as_posix()}"
        if path.is_symlink():
            raise CombRefereeScopeError(f"symlink is outside the scope: {logical}")
        if path.is_dir():
            continue
        records.append(_stable_file_record(path, logical))
    manifest = _file_manifest(records)
    manifest["root"] = logical_root
    return manifest


def _git(args: Sequence[str]) -> bytes:
    proc = subprocess.run(["git", *args], cwd=REPO, capture_output=True)
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise CombRefereeScopeError(
            f"git {' '.join(args)} failed: {detail or 'no diagnostic'}")
    return proc.stdout


def _git_text(args: Sequence[str]) -> str:
    return _git(args).decode("utf-8", errors="strict").strip()


def _git_state() -> dict[str, Any]:
    head = _git_text(("rev-parse", "--verify", "HEAD"))
    tree = _git_text(("rev-parse", "--verify", "HEAD^{tree}"))
    status = _git(("status", "--porcelain=v1", "-z", "--untracked-files=all"))
    return {
        "commit": head,
        "tree": tree,
        "worktree_clean": status == b"",
    }


def _tracked_record(relative: str, head: str) -> dict[str, Any]:
    current = _stable_file_record(REPO / relative, relative)
    head_payload = _git(("show", f"{head}:{relative}"))
    current["head_sha256"] = sha256_bytes(head_payload)
    current["equals_head"] = (
        current["bytes"] == len(head_payload)
        and current["sha256"] == current["head_sha256"]
    )
    return current


def _layout_declared_inputs(
        layout_tree: dict[str, Any], head: str,
        source_root: pathlib.Path = COMB_REFEREE_SOURCE_ROOT,
        ) -> tuple[dict[str, Any], dict[str, Any]]:
    layout_paths = sorted((BUILD / "layout").glob("*.layout.json"))
    if len(layout_paths) != EXPECTED_FORMS:
        raise CombRefereeScopeError(
            f"layout corpus has {len(layout_paths)} files, expected {EXPECTED_FORMS}")
    tree_records = {record["path"]: record for record in layout_tree["files"]}
    provenance: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    seen_slugs: set[str] = set()
    for layout_path in layout_paths:
        slug = layout_path.name.removesuffix(".layout.json")
        if (not slug or slug in seen_slugs
                or any(not (char.isalnum() or char in "-_") for char in slug)):
            raise CombRefereeScopeError(f"invalid or duplicate layout slug: {slug}")
        seen_slugs.add(slug)
        logical_layout = f"build/layout/{layout_path.name}"
        current_layout = _stable_file_record(layout_path, logical_layout)
        if tree_records.get(logical_layout) != current_layout:
            raise CombRefereeScopeError(
                f"layout changed while discovering inputs: {slug}")
        try:
            layout = json.loads(layout_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, RecursionError) as error:
            raise CombRefereeScopeError(f"invalid layout for {slug}: {error}") from error
        source = layout.get("source") if isinstance(layout, dict) else None
        if not isinstance(source, dict):
            raise CombRefereeScopeError(f"layout source is missing: {slug}")
        declared = str(source.get("file", "")).split(":", 1)[-1]
        expected_sha = source.get("sha256")
        expected_bytes = source.get("bytes")
        if (not declared or not _is_sha256(expected_sha)
                or not _is_count(expected_bytes)):
            raise CombRefereeScopeError(f"layout source pin is incomplete: {slug}")

        matches = sorted((FORMS).glob(f"**/{slug}/provenance.json"))
        if len(matches) != 1:
            raise CombRefereeScopeError(
                f"expected one provenance file for {slug}, got {len(matches)}")
        relative_provenance = matches[0].relative_to(REPO).as_posix()
        provenance_record = _tracked_record(relative_provenance, head)
        if not provenance_record["equals_head"]:
            raise CombRefereeScopeError(
                f"provenance differs from HEAD: {relative_provenance}")
        provenance.append(provenance_record)

        try:
            candidates = sorted(
                candidate for candidate in source_root.rglob(declared)
                if candidate.is_file())
        except (OSError, ValueError) as error:
            raise CombRefereeScopeError(
                f"cannot resolve source PDF for {slug}: {error}") from error
        candidate_records = [
            _stable_file_record(
                candidate,
                candidate.relative_to(source_root).as_posix(),
            )
            for candidate in candidates
        ]
        matching = [
            record for record in candidate_records
            if (record["sha256"] == expected_sha
                and record["bytes"] == expected_bytes)
        ]
        if len(matching) != 1:
            raise CombRefereeScopeError(
                f"source PDF has {len(matching)} authoritative matches for "
                f"{slug}; exactly one is required")
        sources.append({
            "slug": slug,
            "declared_file": declared,
            "declared_sha256": expected_sha,
            "declared_bytes": expected_bytes,
            "layout_pin": dict(source),
            "candidate_count": len(candidate_records),
            "matching_count": len(matching),
            "selected": matching[0]["path"],
            "candidates": candidate_records,
        })
    provenance_manifest = _file_manifest(provenance)
    sources_manifest = {
        "relation_count": len(sources),
        "candidate_file_count": sum(item["candidate_count"] for item in sources),
        "sha256": canonical_digest(sources),
        "relations": sources,
    }
    return provenance_manifest, sources_manifest


AUDIT_ASSERTION_SUMMARY_KEYS = (
    "combs_expected", "combs_checked", "expected_comb_ids",
    "checked_comb_ids", "emitted_comb_ids",
    "unexpected_emitted_comb_ids", "duplicate_layout_comb_ids",
    "duplicate_emitted_cell_ids", "raw_live_comb_issues",
    "emitted_cell_binding_issues", "inventory_complete",
    "layout_mismatches", "layout_unevaluable",
    "owner_certificates_valid", "owner_certificates_invalid",
    "source_u_frame_evaluable", "source_certified_unframed_evaluable",
    "emission_behind_layout", "emission_invalid",
    # DECLARED SCHEMA CHANGE (Z1, 2026-08-13): audit.py publishes, for every
    # form and unconditionally, how many comb subjects it settled from the
    # reviewed comb-topology registry and which ones. PROVENANCE, not verdict:
    # a reviewed subject is compared exactly as a measured one, and a reviewed
    # count that disagrees with the emitted count is still an offender. Listed
    # here so every consumer that enumerates the summary keys -- including this
    # file's own fixtures -- carries them, which is what makes the validator
    # below reachable on a well-formed record.
    "decided_by_review", "decided_by_review_subjects",
)
AUDIT_POSITION_FAILURE_KINDS = {
    "emission-layout-position-mismatch",
    "emission-layout-outer-position-mismatch",
    "emission-source-position-mismatch",
    "emission-source-outer-position-mismatch",
    "layout-source-outer-position-mismatch",
}
AUDIT_INVENTORY_FAILURE_KINDS = {
    "duplicate-layout-subject", "unexpected-emitted-comb",
    "emitted-cell-binding-invalid", "duplicate-emitted-cell-id",
    "missing-layout-cell-owner", "duplicate-layout-cell-owner",
    "emitted-cell-page-mismatch", "emitted-cell-geometry-mismatch",
    "unowned-live-comb-markup", "comb-inventory-mismatch",
    "emission-container-page-mismatch",
    "emission-container-geometry-mismatch",
    "comb-owner-registry-invalid",
}
AUDIT_LAYOUT_RELATIONS = {
    "match", "mismatch", "unevaluable", "duplicate-subject",
    "not-owned", "cell-binding-invalid", "inventory-invalid",
    "registry-invalid",
}
AUDIT_FAILURE_KINDS = {
    "source-topology-unevaluable", "layout-printed-mismatch",
    "duplicate-layout-subject", "emission-container-page-mismatch",
    "emission-container-geometry-mismatch",
    "emission-layout-position-mismatch",
    "emission-layout-outer-position-mismatch",
    "emission-source-position-mismatch",
    "emission-source-outer-position-mismatch",
    "layout-source-outer-position-mismatch", "invalid-emission",
    "emission-layout-mismatch", "emission-printed-mismatch",
    "unexpected-emitted-comb", "emitted-cell-binding-invalid",
    "duplicate-emitted-cell-id", "missing-layout-cell-owner",
    "duplicate-layout-cell-owner", "emitted-cell-page-mismatch",
    "emitted-cell-geometry-mismatch", "unowned-live-comb-markup",
    "comb-inventory-mismatch",
    "comb-owner-registry-invalid",
}


def _canonical_decimal_identity(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CombRefereeScopeError("owner-certificate bbox is not numeric")
    try:
        number = Decimal(str(value))
    except InvalidOperation as error:
        raise CombRefereeScopeError(
            "owner-certificate bbox is not decimal") from error
    if not number.is_finite():
        raise CombRefereeScopeError("owner-certificate bbox is not finite")
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def _normalise_owner_certificate(
        value: Any, expected: dict[str, Any] | None,
        ) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CombRefereeScopeError("audit offender owner certificate is missing")
    if value.get("criterion") != "exact-reviewed-layout-comb-subject-owner-v1":
        raise CombRefereeScopeError("audit offender owner criterion is invalid")
    if value.get("valid") is True:
        keys = {
            "criterion", "valid", "layout_sha256", "page", "cell_id",
            "legacy_cell_id", "subject_key", "legacy_bbox",
            "bbox_number_format", "state", "supplies_topology",
        }
        if set(value) != keys or value.get("supplies_topology") is not False:
            raise CombRefereeScopeError(
                "audit offender valid owner certificate schema is false")
        if expected is not None:
            expected_value = {
                "criterion": "exact-reviewed-layout-comb-subject-owner-v1",
                "valid": True,
                "layout_sha256": expected["layout_sha256"],
                "page": expected["page"],
                "cell_id": expected["cell"],
                "legacy_cell_id": expected["legacy_cell_id"],
                "subject_key": expected["subject_key"],
                "legacy_bbox": [
                    _canonical_decimal_identity(item)
                    for item in expected["bbox"]
                ],
                "bbox_number_format": "canonical-decimal-string-v1",
                "state": expected["ledger_state"],
                "supplies_topology": False,
            }
            if value != expected_value:
                raise CombRefereeScopeError(
                    "audit offender owner certificate is not layout-bound")
        return value
    if (set(value) != {"criterion", "valid", "reason", "supplies_topology"}
            or value.get("valid") is not False
            or value.get("supplies_topology") is not False
            or not isinstance(value.get("reason"), str)
            or not value["reason"]):
        raise CombRefereeScopeError(
            "audit offender invalid owner certificate schema is false")
    return value


def _normalise_outer_offender(
        item: Any, expected_owner: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
    """Project trusted raw audit evidence into the child's public relation."""
    if not isinstance(item, dict):
        raise CombRefereeScopeError("audit comb offender is not an object")
    required = {
        "cell", "page", "slots", "latticed", "printed",
        "printed_divider_x", "emission_state", "physical_slots",
        "declared_slots", "emitted_occurrences", "layout_relation",
        "emission_relation", "failure_kinds", "why",
    }
    allowed = required | {
        "slot_indexes", "input_slot_indexes", "slot_geometry",
        "emission_container_binding", "emission_layout_position",
        "emission_layout_outer_position", "emission_source_position",
        "source_frame_geometry", "emission_source_outer_position",
        "layout_source_outer_position", "source_topology_evidence",
        "effective_emission_state", "source_owner_certificate",
        "emitted_cell_binding_evidence", "raw_dom_evidence",
    }
    if not required <= set(item) or set(item) - allowed:
        raise CombRefereeScopeError("audit comb offender schema is incomplete")
    cell = item.get("cell")
    page = item.get("page")
    slots = item.get("slots")
    latticed = item.get("latticed")
    printed = item.get("printed")
    physical = item.get("physical_slots")
    declared_slots = item.get("declared_slots")
    occurrences = item.get("emitted_occurrences")
    layout_relation = item.get("layout_relation")
    emission_state = item.get("emission_state")
    failure_kinds = item.get("failure_kinds")
    if not isinstance(cell, str) or not cell:
        raise CombRefereeScopeError("audit comb offender has no cell identity")
    if page is not None and (not _is_count(page) or page < 1):
        raise CombRefereeScopeError(f"audit comb offender page is invalid: {cell}")
    for name, value in (("slots", slots), ("latticed", latticed),
                        ("printed", printed), ("physical_slots", physical),
                        ("declared_slots", declared_slots)):
        if value is not None and not _is_count(value):
            raise CombRefereeScopeError(
                f"audit comb offender {name} is invalid: {cell}")
    if slots is not None and physical is not None and slots != physical:
        raise CombRefereeScopeError(
            f"audit comb offender physical slot count is false: {cell}")
    divider_x = item.get("printed_divider_x")
    if (not _finite_number_list(divider_x)
            or (printed is None and divider_x)
            or (printed is not None and len(divider_x) != max(0, printed - 1))):
        raise CombRefereeScopeError(
            f"audit comb offender printed topology is invalid: {cell}")
    if not _is_count(occurrences):
        raise CombRefereeScopeError(
            f"audit comb offender occurrences are invalid: {cell}")
    if layout_relation not in AUDIT_LAYOUT_RELATIONS:
        raise CombRefereeScopeError(
            f"audit comb offender layout relation is invalid: {cell}")
    if not isinstance(emission_state, str) or not emission_state:
        raise CombRefereeScopeError(
            f"audit comb offender emission state is invalid: {cell}")
    if (not _string_list(failure_kinds, nonempty=True)
            or set(failure_kinds) - AUDIT_FAILURE_KINDS):
        raise CombRefereeScopeError(
            f"audit comb offender failures are invalid: {cell}")
    if (not isinstance(item.get("emission_relation"), str)
            or not item["emission_relation"]
            or not isinstance(item.get("why"), str) or not item["why"]):
        raise CombRefereeScopeError(
            f"audit comb offender explanation/relation is invalid: {cell}")

    kinds = set(failure_kinds)
    normal_subject = layout_relation in {"match", "mismatch", "unevaluable"}
    if normal_subject and (
            "emission_container_binding" not in item
            or any(field not in item for field in (
                "emission_layout_position", "emission_layout_outer_position",
                "emission_source_position", "emission_source_outer_position",
                "layout_source_outer_position"))):
        raise CombRefereeScopeError(
            f"audit comb offender omits normal-subject geometry: {cell}")
    owner_certificate = item.get("source_owner_certificate")
    if normal_subject or layout_relation == "duplicate-subject":
        owner_certificate = _normalise_owner_certificate(
            owner_certificate, expected_owner)
    elif layout_relation == "registry-invalid":
        owner_certificate = _normalise_owner_certificate(
            owner_certificate, None)
        if (owner_certificate.get("valid") is not False
                or cell != "<comb-owner-registry>"
                or page is not None
                or any(value is not None for value in (
                    slots, latticed, printed, physical, declared_slots))
                or divider_x != [] or occurrences != 0
                or emission_state != "not-evaluated"
                or item.get("effective_emission_state") != "not-evaluated"
                or item.get("emission_relation") != "not-evaluated"
                or failure_kinds != ["comb-owner-registry-invalid"]):
            raise CombRefereeScopeError(
                "audit comb owner-registry offender is malformed")
    elif owner_certificate is not None:
        raise CombRefereeScopeError(
            f"non-owned audit offender invents owner certificate: {cell}")
    position_mismatch = bool(kinds & AUDIT_POSITION_FAILURE_KINDS)
    container_mismatch = bool(kinds & {
        "emission-container-page-mismatch",
        "emission-container-geometry-mismatch",
    })
    binding_invalid = position_mismatch or container_mismatch
    physical_emission_valid = emission_state == "physical-slots"
    emission_invalid = not physical_emission_valid or binding_invalid
    emission_behind = bool(
        layout_relation == "duplicate-subject"
        or not physical_emission_valid
        or binding_invalid
        or (slots is not None and latticed is not None and slots != latticed)
        or kinds & {"unexpected-emitted-comb", "unowned-live-comb-markup"}
    )
    if not normal_subject:
        emission_invalid = bool(
            "unowned-live-comb-markup" in kinds
            or ("unexpected-emitted-comb" in kinds
                and not physical_emission_valid)
            or (layout_relation == "duplicate-subject"
                and not physical_emission_valid)
        )
        emission_behind = bool(
            kinds & {"unexpected-emitted-comb", "unowned-live-comb-markup"}
            or layout_relation == "duplicate-subject"
        )
    dimensions = {
        "layout_mismatch": layout_relation == "mismatch",
        "source_unevaluable": layout_relation in {
            "unevaluable", "duplicate-subject", "inventory-invalid"},
        "emission_invalid": emission_invalid,
        "emission_behind": emission_behind,
        "position_mismatch": position_mismatch,
        "inventory_binding": bool(kinds & AUDIT_INVENTORY_FAILURE_KINDS),
    }
    if not any(dimensions.values()):
        raise CombRefereeScopeError(
            f"audit comb offender has no failure dimension: {cell}")
    return {
        "cell": cell,
        "page": page,
        "slots": slots,
        "latticed": latticed,
        "printed": printed,
        "emitted_occurrences": occurrences,
        "layout_relation": layout_relation,
        "emission_state": emission_state,
        "failure_kinds": failure_kinds,
        "source_owner_certificate": owner_certificate,
        "dimensions": dimensions,
    }


def _layout_audit_owner_ids(layout_binding: Any) -> list[str]:
    """Return the exact ordered non-relocated comb-owner registry.

    ``audit_expected_ids`` is derived from the layout cell stream while
    ``cells`` is derived independently from the reviewed subject ledger.  A
    summary count is trustworthy only when those two byte-bound projections
    name the same active emission owners in the same order.
    """
    if not isinstance(layout_binding, dict):
        raise CombRefereeScopeError("parsed layout-owner registry is missing")
    audit_ids = layout_binding.get("audit_expected_ids")
    cells = layout_binding.get("cells")
    if (not isinstance(audit_ids, list)
            or not all(isinstance(item, str) and item for item in audit_ids)
            or len(audit_ids) != len(set(audit_ids))
            or not isinstance(cells, dict)):
        raise CombRefereeScopeError(
            "parsed layout-owner registry is malformed")
    owner_ids: list[str] = []
    for cell_id, cell in cells.items():
        if (not isinstance(cell_id, str) or not cell_id
                or not isinstance(cell, dict)
                or cell.get("cell") != cell_id):
            raise CombRefereeScopeError(
                "parsed layout-owner registry has a malformed subject")
        page = cell.get("page")
        stream_index = cell.get("stream_index")
        if (not isinstance(page, int) or isinstance(page, bool) or page < 1
                or not isinstance(stream_index, int)
                or isinstance(stream_index, bool) or stream_index < 0):
            raise CombRefereeScopeError(
                "parsed layout-owner registry subject has no stream position")
    # JSON object keys are intentionally serialized with sort_keys=True in
    # both the referee report envelope and the persisted audit envelope.  Do
    # not let that lexical reordering change the owner registry: order it by
    # the stream position each subject recorded when the layout was parsed.
    # ``audit_expected_ids`` is appended during that same stream walk, so both
    # sides of the equality below are the layout cell stream and neither is a
    # re-derivation from the cell numerals -- an id is a continuity
    # identifier, not a geometric one.  Validate the registry above before
    # sorting so malformed hostile entries still fail closed rather than being
    # hidden by a fallback.
    owner_ids = [
        cell_id for cell_id, cell in sorted(
            cells.items(), key=_layout_subject_sort_key)
        if (cell.get("ledger_state") in {
                "active_resolved", "active_unresolved"}
                and cell.get("expected_emission_geometry") is not None)
        # active_composite is deliberately absent: a composite emits nothing
        # of its own, so it owns no emission geometry to register.
    ]
    if audit_ids != owner_ids:
        raise CombRefereeScopeError(
            "layout cell and reviewed-subject owner registries differ")
    return owner_ids


def _normalise_outer_comb_assertion(
        assertion: Any, layout_binding: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
    """Require exhaustive offender publication and expose an exact cell map."""
    if not isinstance(assertion, dict):
        raise CombRefereeScopeError("comb audit assertion is missing")
    missing = [key for key in AUDIT_ASSERTION_SUMMARY_KEYS
               if key not in assertion]
    if missing:
        raise CombRefereeScopeError(
            "comb audit assertion omits: " + ", ".join(missing))
    holds = assertion.get("holds")
    offenders = assertion.get("offenders")
    if not isinstance(holds, bool) or not isinstance(offenders, list):
        raise CombRefereeScopeError(
            "comb audit verdict/offender inventory is malformed")
    expected_keys = {
        *AUDIT_ASSERTION_SUMMARY_KEYS, "holds", "reason", "offenders",
    }
    if holds is False:
        expected_keys |= BASIC_ASSERTION_PUBLICATION_KEYS
    if set(assertion) != expected_keys:
        raise CombRefereeScopeError(
            "comb audit assertion schema is incomplete or unsupported")
    reviewed_count = assertion.get("decided_by_review")
    reviewed_subjects = assertion.get("decided_by_review_subjects")
    if (not _is_count(reviewed_count)
            or not isinstance(reviewed_subjects, list)
            or len(reviewed_subjects) != reviewed_count):
        raise CombRefereeScopeError(
            "comb audit reviewed-topology publication is malformed")
    reviewed_cells: list[str] = []
    for subject in reviewed_subjects:
        # Each subject carries its own evidence, not just an id, so the record
        # a reader audits later is self-contained: which cell, what the lattice
        # and the reviewed fact each said, and the fact's own provenance.
        # `latticed == printed == compartments` is required HERE rather than
        # trusted: a reviewed subject that did not actually agree would be an
        # offender, so it must never reach this list.
        certificate = (subject or {}).get("reviewed_comb_topology") \
            if isinstance(subject, dict) else None
        if (not isinstance(subject, dict)
                or not isinstance(certificate, dict)
                or certificate.get("criterion") != "reviewed-comb-topology-v1"
                or certificate.get("valid") is not True
                or not isinstance(subject.get("cell"), str)
                or not subject.get("cell")
                or not _is_count(subject.get("printed"))
                or not _is_count(subject.get("latticed"))
                or subject.get("printed") != subject.get("latticed")
                or certificate.get("compartments") != subject.get("printed")
                or not isinstance(certificate.get("source_sha256"), str)
                or not isinstance(certificate.get("reviewer"), str)
                or not certificate.get("reviewer")
                or not isinstance(certificate.get("citation"), str)
                or not certificate.get("citation")):
            raise CombRefereeScopeError(
                "comb audit reviewed-topology publication is malformed")
        reviewed_cells.append(subject["cell"])
    if len(set(reviewed_cells)) != len(reviewed_cells):
        raise CombRefereeScopeError(
            "comb audit reviewed-topology publication is malformed")
    expected_cell_ids = assertion.get("expected_comb_ids")
    if isinstance(expected_cell_ids, list) and any(
            cell not in expected_cell_ids for cell in reviewed_cells):
        # The registry cannot smuggle in a cell this form does not have.
        raise CombRefereeScopeError(
            "comb audit reviewed-topology publication names an unknown cell")
    reason = assertion.get("reason")
    if (not isinstance(reason, str)
            or (holds and reason != "")
            or (not holds and not reason)):
        raise CombRefereeScopeError(
            "comb audit assertion reason is inconsistent")
    expected_ids = assertion.get("expected_comb_ids")
    checked_ids = assertion.get("checked_comb_ids")
    if (not _is_count(assertion.get("combs_expected"))
            or not _is_count(assertion.get("combs_checked"))
            or not isinstance(expected_ids, list)
            or not all(isinstance(item, str) and item for item in expected_ids)
            or len(expected_ids) != len(set(expected_ids))
            or checked_ids != expected_ids
            or assertion.get("combs_expected") != len(expected_ids)
            or assertion.get("combs_checked") != len(expected_ids)):
        raise CombRefereeScopeError(
            "comb audit checked inventory is incomplete or duplicated")
    for key in (
            "emitted_comb_ids", "unexpected_emitted_comb_ids",
            "duplicate_layout_comb_ids", "duplicate_emitted_cell_ids"):
        values = assertion.get(key)
        if (not isinstance(values, list)
                or not all(isinstance(item, str) and item for item in values)
                or len(values) != len(set(values))):
            raise CombRefereeScopeError(
                f"comb audit inventory is malformed: {key}")
    for key in (
            "raw_live_comb_issues", "emitted_cell_binding_issues",
            "layout_mismatches", "layout_unevaluable",
            "owner_certificates_valid", "owner_certificates_invalid",
            "source_u_frame_evaluable",
            "source_certified_unframed_evaluable",
            "emission_behind_layout", "emission_invalid"):
        if not _is_count(assertion.get(key)):
            raise CombRefereeScopeError(
                f"comb audit count is malformed: {key}")
    if not isinstance(assertion.get("inventory_complete"), bool):
        raise CombRefereeScopeError(
            "comb audit inventory-complete flag is malformed")
    if (assertion["owner_certificates_valid"]
            + assertion["owner_certificates_invalid"]
            != assertion["combs_checked"]):
        raise CombRefereeScopeError(
            "comb audit owner-certificate partition is false")

    count = assertion.get("offender_count", 0 if holds else None)
    published = assertion.get("offenders_published", 0 if holds else None)
    omitted = assertion.get("offenders_omitted", 0 if holds else None)
    complete = assertion.get("offenders_complete", True if holds else None)
    if (not _is_count(count) or not _is_count(published)
            or not _is_count(omitted) or not isinstance(complete, bool)
            or count != len(offenders) or published != len(offenders)
            or count != published + omitted or omitted != 0
            or complete is not True or (holds and offenders)):
        raise CombRefereeScopeError(
            "comb audit offender publication is truncated or inconsistent")

    dimensions: dict[str, Any] = {}
    raw_offenders_by_cell: dict[str, dict[str, Any]] = {}
    for raw in offenders:
        raw_cell = raw.get("cell") if isinstance(raw, dict) else None
        expected_owner = None
        if isinstance(layout_binding, dict) and isinstance(raw_cell, str):
            projected = layout_binding.get("cells", {}).get(raw_cell)
            if isinstance(projected, dict):
                expected_owner = {
                    **projected,
                    "layout_sha256": layout_binding.get("layout_sha256"),
                }
        relation = _normalise_outer_offender(raw, expected_owner)
        cell = relation["cell"]
        if cell in dimensions:
            raise CombRefereeScopeError(
                f"comb audit publishes duplicate offender: {cell}")
        dimensions[cell] = relation
        raw_offenders_by_cell[cell] = raw
    expected_set = set(expected_ids)
    emitted_ids = assertion["emitted_comb_ids"]
    emitted_set = set(emitted_ids)
    unexpected_ids = assertion["unexpected_emitted_comb_ids"]
    duplicate_layout = assertion["duplicate_layout_comb_ids"]
    duplicate_emitted = assertion["duplicate_emitted_cell_ids"]
    if (emitted_ids != sorted(emitted_ids)
            or unexpected_ids != sorted(emitted_set - expected_set)):
        raise CombRefereeScopeError(
            "comb audit emitted/unexpected inventories are not derived")
    if any(cell_id not in expected_set for cell_id in duplicate_layout):
        raise CombRefereeScopeError(
            "comb audit duplicate-layout inventory has no expected owner")
    if duplicate_layout != sorted(duplicate_layout):
        raise CombRefereeScopeError(
            "comb audit duplicate-layout inventory is not canonical")
    if duplicate_emitted != sorted(duplicate_emitted):
        raise CombRefereeScopeError(
            "comb audit duplicate-emitted inventory is not canonical")

    unexpected_offenders: set[str] = set()
    duplicate_layout_offenders: set[str] = set()
    raw_live_issues = 0
    binding_issue_cells: set[str] = set()
    inventory_failure = False
    for cell_id, relation in dimensions.items():
        kinds = set(relation["failure_kinds"])
        layout_relation = relation["layout_relation"]
        if layout_relation in {"match", "mismatch", "unevaluable"}:
            if cell_id not in expected_set:
                raise CombRefereeScopeError(
                    f"comb audit normal offender is orphaned: {cell_id}")
        elif layout_relation == "duplicate-subject":
            if cell_id not in duplicate_layout:
                raise CombRefereeScopeError(
                    f"comb audit duplicate offender is unlisted: {cell_id}")
            duplicate_layout_offenders.add(cell_id)
        elif "unexpected-emitted-comb" in kinds:
            if cell_id not in unexpected_ids or cell_id not in emitted_set:
                raise CombRefereeScopeError(
                    f"comb audit unexpected offender is orphaned: {cell_id}")
            unexpected_offenders.add(cell_id)
            binding_issue_cells.add(cell_id)
        elif "unowned-live-comb-markup" in kinds:
            raw_live_issues += 1
        elif "emitted-cell-binding-invalid" in kinds:
            binding_issue_cells.add(cell_id)
        elif layout_relation == "registry-invalid":
            if (cell_id != "<comb-owner-registry>"
                    or kinds != {"comb-owner-registry-invalid"}):
                raise CombRefereeScopeError(
                    "comb audit owner-registry offender identity is invalid")
            inventory_failure = True
        elif "comb-inventory-mismatch" in kinds:
            if cell_id != "<comb-inventory>":
                raise CombRefereeScopeError(
                    "comb audit inventory offender identity is invalid")
            inventory_failure = True
        else:
            raise CombRefereeScopeError(
                f"comb audit offender has no declared inventory owner: {cell_id}")
        if kinds & {
                "emission-container-page-mismatch",
                "emission-container-geometry-mismatch"}:
            binding_issue_cells.add(cell_id)

    if unexpected_offenders != set(unexpected_ids):
        raise CombRefereeScopeError(
            "comb audit unexpected inventory/offenders disagree")
    if duplicate_layout_offenders != set(duplicate_layout):
        raise CombRefereeScopeError(
            "comb audit duplicate-layout inventory/offenders disagree")
    published_certificates = {
        cell_id: relation["source_owner_certificate"]
        for cell_id, relation in dimensions.items()
        if isinstance(relation.get("source_owner_certificate"), dict)
        and relation.get("layout_relation") in {
            "match", "mismatch", "unevaluable", "duplicate-subject"}
    }
    published_valid = sum(
        certificate.get("valid") is True
        for certificate in published_certificates.values())
    published_invalid = len(published_certificates) - published_valid
    if (published_valid > assertion["owner_certificates_valid"]
            or published_invalid > assertion["owner_certificates_invalid"]):
        raise CombRefereeScopeError(
            "comb audit published owner certificates exceed summary counts")
    if set(published_certificates) == set(checked_ids) and (
            assertion["owner_certificates_valid"] != published_valid
            or assertion["owner_certificates_invalid"] != published_invalid):
        raise CombRefereeScopeError(
            "comb audit complete owner-certificate publication disagrees "
            "with summary counts")
    if isinstance(layout_binding, dict):
        projected_ids = _layout_audit_owner_ids(layout_binding)
        if (expected_ids != projected_ids
                or assertion["owner_certificates_valid"] != len(expected_ids)
                or assertion["owner_certificates_invalid"] != 0):
            raise CombRefereeScopeError(
                "comb audit owner-certificate summary disagrees with the "
                "exact parsed layout-owner registry")
    checked_source_unevaluable = {
        cell_id for cell_id, relation in dimensions.items()
        if cell_id in expected_set
        and relation["dimensions"]["source_unevaluable"]
    }
    source_evaluable = (
        assertion["combs_checked"] - len(checked_source_unevaluable))
    # DECLARED SCHEMA CHANGE (Z1): a reviewed-topology decision is a third
    # evaluability class.  Such a cell is no longer published as an offender,
    # so it is NOT in checked_source_unevaluable and counts toward
    # source_evaluable -- yet its compartment count came from review, not from
    # a U-frame or an unframed certificate, so neither source term covers it.
    # The partition is therefore three-way.  This does not widen what passes:
    # every reviewed cell was already validated above to carry a valid
    # certificate with a named reviewer and citation and printed == latticed,
    # which is stricter per-cell than either source class.  Disjointness is
    # asserted rather than assumed -- a cell that is both reviewed and still
    # published source-unevaluable means the registry failed to clear it.
    reviewed_set = set(reviewed_cells)
    if reviewed_set & checked_source_unevaluable:
        raise CombRefereeScopeError(
            "comb audit counts a reviewed cell as source-unevaluable")
    if (assertion["source_u_frame_evaluable"]
            + assertion["source_certified_unframed_evaluable"]
            + len(reviewed_set)
            != source_evaluable):
        raise CombRefereeScopeError(
            "comb audit source frame/unframed/reviewed counts do not "
            "partition evaluable checked cells")
    published_u_frame = 0
    published_certified_unframed = 0
    for cell_id, relation in dimensions.items():
        if cell_id not in expected_set or relation["printed"] is None:
            continue
        certificate = relation.get("source_owner_certificate")
        if (not isinstance(certificate, dict)
                or certificate.get("valid") is not True):
            raise CombRefereeScopeError(
                f"comb audit measured source lacks a valid owner "
                f"certificate: {cell_id}")
        if raw_offenders_by_cell[cell_id].get("source_frame_geometry") is None:
            published_certified_unframed += 1
        else:
            published_u_frame += 1
    if published_u_frame > assertion["source_u_frame_evaluable"]:
        raise CombRefereeScopeError(
            "comb audit published U-frame source results exceed their count")
    if (published_certified_unframed
            > assertion["source_certified_unframed_evaluable"]):
        raise CombRefereeScopeError(
            "comb audit published certified-unframed source results exceed "
            "their count")
    for cell_id in expected_set - emitted_set:
        relation = dimensions.get(cell_id)
        if (not isinstance(relation, dict)
                or relation.get("emission_state") != "missing-emitted-cell"
                or not set(relation.get("failure_kinds", [])) & {
                    "invalid-emission", "duplicate-layout-subject"}):
            raise CombRefereeScopeError(
                f"comb audit omits missing-emission offender: {cell_id}")
    if any(cell_id not in dimensions for cell_id in duplicate_emitted):
        raise CombRefereeScopeError(
            "comb audit duplicate-emitted inventory has no offender")
    binding_issue_cells.update(duplicate_emitted)
    binding_issue_cells.update(
        cell_id for cell_id in duplicate_layout if cell_id in emitted_set)
    derived_counts = {
        "layout_mismatches": sum(
            relation["dimensions"]["layout_mismatch"]
            for relation in dimensions.values()),
        "layout_unevaluable": sum(
            relation["dimensions"]["source_unevaluable"]
            for relation in dimensions.values()),
        "emission_behind_layout": sum(
            relation["dimensions"]["emission_behind"]
            for relation in dimensions.values()),
        "emission_invalid": sum(
            relation["dimensions"]["emission_invalid"]
            for relation in dimensions.values()),
        "raw_live_comb_issues": raw_live_issues,
        "emitted_cell_binding_issues": len(binding_issue_cells),
    }
    for key, derived in derived_counts.items():
        if assertion.get(key) != derived:
            raise CombRefereeScopeError(
                f"comb audit summary counter is false: {key}")
    derived_inventory_complete = not (
        unexpected_ids
        or duplicate_layout
        or any(cell_id in expected_set or cell_id in emitted_set
               for cell_id in duplicate_emitted)
        or inventory_failure
        or raw_live_issues
        or binding_issue_cells
    )
    if assertion["inventory_complete"] is not derived_inventory_complete:
        raise CombRefereeScopeError(
            "comb audit inventory-complete relation is false")
    if holds is not (not dimensions):
        raise CombRefereeScopeError(
            "comb audit holds verdict disagrees with exhaustive offenders")
    return {
        **{key: assertion[key] for key in AUDIT_ASSERTION_SUMMARY_KEYS},
        "offender_count": count,
        "offenders_published": published,
        "offenders_omitted": omitted,
        "offender_dimensions": dimensions,
        "holds": holds,
    }


AUDIT_APPLICATION_ENVELOPE_KEYS = {
    "schema_version", "application_scope_name", "application_snapshot",
    "invocation", "raw_report", "relations", "host_tcb_required",
    "host_scope_complete", "host_closure_claimed", "operating_system_bound",
    "python_stdlib_bound", "dynamic_libraries_bound",
    "application_scope_complete", "enforceable", "enforcement_scope",
    "self_digest", "payload_sha256",
}
AUDIT_APPLICATION_INVOCATION_KEYS = {
    "executable", "resolved_executable", "python_flags",
    "pythonpath_removed", "pythonhome_removed", "timeout_seconds", "output",
    "target_argv", "child_exit", "launcher_receipt",
}
AUDIT_APPLICATION_RAW_KEYS = {
    "file", "bytes", "sha256", "form_count",
}
AUDIT_APPLICATION_RELATIONS = {
    "clean_revision_before_after",
    "tracked_producers_equal_head_before_after",
    "declared_inputs_hashed_before_after",
    "per_form_input_manifests_bound_to_application_snapshot",
    "python_executable_hashed_before_after",
    "sanitized_python_environment",
    "isolated_python_mode",
    "isolated_dependencies_bound",
    "verified_fd_dependency_materialization",
    "fresh_isolated_pycache_prefix",
    "hard_timeout_enforced",
    "process_group_supervised",
    "subprocess_popen_python_rewrite_installed",
    "os_process_control_guards_installed",
    "supervised_process_group_cleanup_attested",
    "audit_report_schema_valid",
    "validated_output_only",
    "atomic_report_publish",
    "atomic_envelope_publish",
}


def isolated_launch_receipt_errors(
        value: Any, dependency_manifest: Any, child_exit: int,
        resolved_executable: str,
        expected_target_argv: Sequence[str] | None = None,
        ) -> list[str]:
    errors: list[str] = []
    if not _is_count(child_exit):
        errors.append("isolated launch expected child exit is malformed")
    expected_keys = {
        "schema", "bootstrap", "dependency_manifest", "command_flags",
        "pythonpath_removed", "pythonhome_removed",
        "source_dependencies_copied_from_verified_fds",
        "private_dependencies_validated_before_after",
        "process_group_supervised",
        "subprocess_popen_python_rewrite_installed",
        "os_process_control_guards_installed",
        "supervised_group_quiescent", "timed_out", "cleanup_complete",
        "child_exit",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        return ["isolated launch receipt schema is unsupported"]
    bootstrap = value.get("bootstrap")
    bootstrap_keys = {
        "schema", "executable", "isolated", "no_site",
        "dont_write_bytecode", "pycache_prefix", "cwd",
        "pythonpath_absent", "pythonhome_absent", "site_not_loaded",
        "bootstrap_sha256", "spec_sha256", "dependency_manifest_sha256",
        "target_argv_sha256", "worker_exit", "target_exit",
        "recursive_launcher_installed", "process_group_supervised",
        "subprocess_popen_python_rewrite_installed",
        "os_process_control_guards_installed",
        "lingering_descendants_detected",
        "cleanup_complete",
    }
    if not isinstance(bootstrap, dict) or set(bootstrap) != bootstrap_keys:
        errors.append("isolated bootstrap receipt schema is unsupported")
        bootstrap = {}
    for key in (
            "bootstrap_sha256", "spec_sha256", "target_argv_sha256"):
        if not _is_sha256(bootstrap.get(key)):
            errors.append(f"isolated bootstrap receipt has invalid {key}")
    if (expected_target_argv is not None
            and bootstrap.get("target_argv_sha256")
            != _compact_digest(list(expected_target_argv))):
        errors.append("isolated bootstrap receipt targets another invocation")
    pycache_prefix = bootstrap.get("pycache_prefix")
    pycache_path = (
        pathlib.Path(pycache_prefix) if isinstance(pycache_prefix, str)
        else pathlib.Path())
    resolved_pycache = pycache_path.resolve(strict=False)
    temporary_root = pathlib.Path(tempfile.gettempdir()).resolve()
    if (not isinstance(pycache_prefix, str)
            or not pycache_path.is_absolute()
            or ".." in pycache_path.parts
            or resolved_pycache.name != "pycache"
            or not resolved_pycache.parent.name.startswith(
                ".gate-python-isolated-")
            or resolved_pycache.parent.parent != temporary_root
            or resolved_pycache == REPO
            or REPO in resolved_pycache.parents):
        errors.append("isolated bootstrap pycache prefix is unsafe")
    if (bootstrap.get("schema")
            != "formgen-isolated-python-bootstrap-receipt-v2"
            or bootstrap.get("executable") != resolved_executable
            or type(bootstrap.get("isolated")) is not int
            or bootstrap.get("isolated") != 1
            or type(bootstrap.get("no_site")) is not int
            or bootstrap.get("no_site") != 1
            or bootstrap.get("dont_write_bytecode") is not True
            or bootstrap.get("cwd") != str(REPO.resolve())
            or bootstrap.get("pythonpath_absent") is not True
            or bootstrap.get("pythonhome_absent") is not True
            or bootstrap.get("site_not_loaded") is not True
            or bootstrap.get("recursive_launcher_installed") is not True
            or bootstrap.get("process_group_supervised") is not True
            or bootstrap.get(
                "subprocess_popen_python_rewrite_installed") is not True
            or bootstrap.get(
                "os_process_control_guards_installed") is not True
            or bootstrap.get("lingering_descendants_detected") is not False
            or bootstrap.get("cleanup_complete") is not True
            or not _is_count(bootstrap.get("worker_exit"))
            or bootstrap.get("worker_exit") != child_exit
            or not _is_count(bootstrap.get("target_exit"))
            or bootstrap.get("target_exit") != child_exit):
        errors.append("isolated bootstrap observations are incomplete")
    if (not isinstance(dependency_manifest, dict)
            or not _json_type_exact_equal(
                value.get("dependency_manifest"), dependency_manifest)
            or bootstrap.get("dependency_manifest_sha256")
            != dependency_manifest.get("sha256")):
        errors.append("isolated dependency manifest is stale or unbound")
    if (value.get("schema") != "formgen-isolated-python-launch-receipt-v2"
            or value.get("command_flags") != ISOLATED_PYTHON_ATTESTED_FLAGS
            or value.get("pythonpath_removed") is not True
            or value.get("pythonhome_removed") is not True
            or value.get(
                "source_dependencies_copied_from_verified_fds") is not True
            or value.get("private_dependencies_validated_before_after") is not True
            or value.get("process_group_supervised") is not True
            or value.get(
                "subprocess_popen_python_rewrite_installed") is not True
            or value.get("os_process_control_guards_installed") is not True
            or value.get("supervised_group_quiescent") is not True
            or value.get("timed_out") is not False
            or value.get("cleanup_complete") is not True
            or not _is_count(value.get("child_exit"))
            or value.get("child_exit") != child_exit):
        errors.append("isolated parent launch observations are incomplete")
    return errors


def validate_audit_application_envelope(
        envelope: Any, audit_payload: bytes,
        current_scope: dict[str, Any] | None = None,
        ) -> list[str]:
    errors: list[str] = []
    if not isinstance(envelope, dict):
        return ["audit application envelope is not an object"]
    if set(envelope) != AUDIT_APPLICATION_ENVELOPE_KEYS:
        errors.append("audit application envelope schema is unsupported")
    if envelope.get("schema_version") != AUDIT_APPLICATION_ATTESTATION_VERSION:
        errors.append("audit application envelope version is unsupported")
    if envelope.get("application_scope_name") != AUDIT_APPLICATION_SCOPE:
        errors.append("audit application envelope scope is wrong")
    if not self_digest_valid(envelope):
        errors.append("audit application envelope self-digest is stale")
    relations = envelope.get("relations")
    if (not isinstance(relations, dict)
            or set(relations) != AUDIT_APPLICATION_RELATIONS
            or any(value is not True for value in relations.values())):
        errors.append("audit application relations are incomplete")
    boundary = {
        "host_tcb_required": True,
        "host_scope_complete": False,
        "host_closure_claimed": False,
        "operating_system_bound": False,
        "python_stdlib_bound": False,
        "dynamic_libraries_bound": False,
        "application_scope_complete": True,
        "enforceable": True,
        "enforcement_scope": "application-only",
    }
    for key, expected in boundary.items():
        if (envelope.get(key) is not expected
                if isinstance(expected, bool)
                else envelope.get(key) != expected):
            errors.append(f"audit application boundary is invalid: {key}")
    snapshot = envelope.get("application_snapshot")
    if not isinstance(snapshot, dict):
        errors.append("audit application snapshot is missing")
        snapshot = {}
    if (current_scope is not None
            and not _json_type_exact_equal(snapshot, current_scope)):
        errors.append("audit application envelope is stale")
    invocation = envelope.get("invocation")
    if not isinstance(invocation, dict):
        errors.append("audit application invocation is missing")
        invocation = {}
    elif set(invocation) != AUDIT_APPLICATION_INVOCATION_KEYS:
        errors.append("audit application invocation schema is unsupported")
    snapshot_runtime = snapshot.get("runtime")
    if not isinstance(snapshot_runtime, dict):
        errors.append("audit application runtime snapshot is malformed")
        snapshot_runtime = {}
    snapshot_python = snapshot_runtime.get("python")
    if not isinstance(snapshot_python, dict):
        errors.append("audit application Python snapshot is malformed")
        snapshot_python = {}
    snapshot_dependencies = snapshot_runtime.get("python_dependencies")
    target_argv = invocation.get("target_argv")
    target_path: pathlib.Path | None = None
    if (not isinstance(target_argv, list)
            or len(target_argv) != 3
            or any(not isinstance(value, str) for value in target_argv)):
        errors.append("audit application target argv is malformed")
        target_argv = []
    else:
        target_path = pathlib.Path(target_argv[2])
        if (target_argv[:2] != [str(HERE / "audit.py"), "--out"]
                or not target_path.is_absolute()
                or target_path.name != "audit.json"
                or not target_path.parent.name.startswith(".full-audit-")
                or target_path == AUDIT_JSON):
            errors.append("audit application target argv is not the private audit")
    if (invocation.get("executable") != sys.executable
            or invocation.get("resolved_executable")
            != snapshot_python.get("path")
            or invocation.get("python_flags")
            != ISOLATED_PYTHON_ATTESTED_FLAGS
            or invocation.get("pythonpath_removed") is not True
            or invocation.get("pythonhome_removed") is not True
            or invocation.get("timeout_seconds") != 5400
            or invocation.get("output") != "private-temporary-output"
            or not _is_count(invocation.get("child_exit"))
            or invocation.get("child_exit") != 0):
        errors.append("audit application invocation contract is incomplete")
    errors.extend(isolated_launch_receipt_errors(
        invocation.get("launcher_receipt"), snapshot_dependencies, 0,
        str(snapshot_python.get("path", "")), target_argv or None))
    raw = envelope.get("raw_report")
    if not isinstance(raw, dict):
        errors.append("audit application raw-report identity is missing")
        raw = {}
    elif set(raw) != AUDIT_APPLICATION_RAW_KEYS:
        errors.append("audit application raw-report schema is unsupported")
    try:
        audit_data = json.loads(audit_payload)
        form_count = len(audit_data)
    except (UnicodeError, ValueError, RecursionError, TypeError):
        audit_data = None
        form_count = -1
    if (raw.get("file") != "build/audit.json"
            or not _is_count(raw.get("bytes"))
            or raw.get("bytes") != len(audit_payload)
            or raw.get("sha256") != sha256_bytes(audit_payload)
            or not _is_count(raw.get("form_count"))
            or raw.get("form_count") != form_count):
        errors.append("audit application raw report is stale or unbound")
    errors.extend(audit_payload_snapshot_binding_errors(audit_data, snapshot))
    return errors


def _audit_snapshot(application_scope: dict[str, Any]) -> dict[str, Any]:
    record = _stable_file_record(AUDIT_JSON, "build/audit.json")
    try:
        payload = AUDIT_JSON.read_bytes()
        data = json.loads(payload)
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        raise CombRefereeScopeError(f"audit JSON is malformed: {error}") from error
    if (sha256_bytes(payload) != record["sha256"]
            or not isinstance(data, list) or len(data) != EXPECTED_FORMS):
        raise CombRefereeScopeError(
            "audit JSON changed while hashing or has incomplete corpus coverage")
    try:
        envelope_payload = AUDIT_APPLICATION_ATTESTATION.read_bytes()
        envelope = json.loads(envelope_payload)
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        raise CombRefereeScopeError(
            f"audit application attestation is malformed: {error}") from error
    envelope_errors = validate_audit_application_envelope(
        envelope, payload, application_scope)
    if envelope_errors:
        raise CombRefereeScopeError("; ".join(envelope_errors[:5]))
    envelope_record = _stable_file_record(
        AUDIT_APPLICATION_ATTESTATION, "build/audit-attested.json")
    if envelope_record["sha256"] != sha256_bytes(envelope_payload):
        raise CombRefereeScopeError(
            "audit application attestation changed while hashing")
    payload_errors = full_audit_payload_errors(data, canonical_form_slugs())
    if payload_errors:
        raise CombRefereeScopeError(
            "audit JSON is not a complete producer-shaped report: "
            + "; ".join(payload_errors[:5]))
    forms: dict[str, Any] = {}
    for audit_form in data:
        if not isinstance(audit_form, dict):
            raise CombRefereeScopeError("audit JSON contains a non-object form")
        slug = audit_form.get("slug")
        manifest = audit_form.get("input_manifest")
        assertions = audit_form.get("assertions")
        assertion = (assertions.get("comb_slots_match_printed")
                     if isinstance(assertions, dict) else None)
        if (not isinstance(slug, str) or not slug or slug in forms
                or not isinstance(manifest, dict)
                or not isinstance(manifest.get("inputs"), dict)
                or not isinstance(manifest.get("render"), dict)
                or not isinstance(assertion, dict)):
            raise CombRefereeScopeError(
                f"audit per-form input relation is incomplete: {slug}")
        layout_binding = application_scope.get("layout_bindings", {}).get(slug)
        if not isinstance(layout_binding, dict):
            raise CombRefereeScopeError(
                f"audit form has no parsed layout binding: {slug}")
        assertion_relation = _normalise_outer_comb_assertion(
            assertion, layout_binding)
        forms[slug] = {
            "record_sha256": canonical_digest(audit_form),
            "input_manifest_sha256": canonical_digest(manifest),
            "inputs": manifest["inputs"],
            "render": manifest["render"],
            "assertion_sha256": canonical_digest(assertion),
            "assertion_relation": assertion_relation,
            "top_level_holds": audit_form.get("comb_slots_match_printed"),
        }
    if set(forms) != set(canonical_form_slugs()):
        raise CombRefereeScopeError(
            "audit JSON does not match the exact tracked form corpus")
    record["form_count"] = len(forms)
    record["forms_sha256"] = canonical_digest(forms)
    record["forms"] = forms
    record["application_attestation"] = envelope_record
    record["application_scope_attested"] = True
    return record


class GateAuditRenderDependencyScanner(html.parser.HTMLParser):
    """Independent local-resource inventory for the audit application gate."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[tuple[str, str]] = []
        self.errors: list[str] = []
        self.style_depth = 0

    def _add(self, value: str | None, kind: str) -> None:
        if value is not None and value.strip():
            self.references.append((value.strip(), kind))

    def _srcset(self, value: str | None, kind: str) -> None:
        if value is None:
            return
        # A data URL may itself contain commas, so this deliberately refuses
        # to pretend the simple srcset splitter can inventory that closure.
        if "data:" in value.lower():
            self.errors.append(
                "data URLs in srcset are unsupported by the closure parser")
            return
        for candidate in value.split(","):
            parts = candidate.strip().split()
            if parts:
                self._add(parts[0], kind)

    def handle_starttag(
            self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value for key, value in attrs}
        lowered = tag.lower()
        if lowered == "base" and values.get("href"):
            self.errors.append(
                "base href is forbidden in an isolated render snapshot")
        if (lowered == "meta"
                and (values.get("http-equiv") or "").lower() == "refresh"):
            self.errors.append(
                "meta refresh is forbidden in an isolated render snapshot")
        if lowered == "style":
            self.style_depth += 1
        self.references.extend(
            (url, "inline-style")
            for url in _gate_audit_css_urls(values.get("style") or ""))
        if lowered == "script":
            self._add(values.get("src"), "script")
        elif lowered == "link":
            rel = {item.lower() for item in (values.get("rel") or "").split()}
            if rel & {"stylesheet", "preload", "modulepreload", "icon", "manifest"}:
                self._add(values.get("href"), "link")
        elif lowered in {"img", "source"}:
            self._add(values.get("src"), lowered)
            self._srcset(values.get("srcset"), f"{lowered}-srcset")
        elif lowered in {"video", "audio", "track", "embed", "iframe"}:
            self._add(values.get("src"), lowered)
            if lowered == "video":
                self._add(values.get("poster"), "video-poster")
        elif lowered == "object":
            self._add(values.get("data"), "object")
        elif lowered == "input" and (
                values.get("type") or "").lower() == "image":
            self._add(values.get("src"), "input-image")
        elif lowered == "image":
            self._add(
                values.get("href") or values.get("xlink:href"), "svg-image")

    def handle_startendtag(
            self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() == "style" and self.style_depth:
            self.style_depth -= 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "style" and self.style_depth:
            self.style_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.style_depth:
            self.references.extend(
                (url, "style-block") for url in _gate_audit_css_urls(data))


_GATE_AUDIT_CSS_URL_RE = re.compile(
    r"""url\(\s*(?P<quote>["']?)(?P<url>.*?)(?P=quote)\s*\)""",
    re.IGNORECASE,
)
_GATE_AUDIT_CSS_IMPORT_RE = re.compile(
    r"""@import\s+(?:url\(\s*)?(?P<quote>["'])(?P<url>.*?)(?P=quote)""",
    re.IGNORECASE,
)


def _gate_audit_css_urls(css: str) -> list[str]:
    return [
        *(match.group("url")
          for match in _GATE_AUDIT_CSS_IMPORT_RE.finditer(css)),
        *(match.group("url")
          for match in _GATE_AUDIT_CSS_URL_RE.finditer(css)),
    ]


def _gate_audit_logical_resource(reference: str, base: str) -> str | None:
    parsed = urllib.parse.urlsplit(reference.strip())
    if parsed.scheme.lower() == "data":
        return None
    if (parsed.scheme or parsed.netloc or reference.startswith("//")
            or parsed.path.startswith("/") or parsed.query):
        raise CombRefereeScopeError(
            f"external, absolute, or query-bearing render resource: {reference}")
    if not parsed.path:
        return None
    decoded = urllib.parse.unquote(parsed.path)
    if ("\\" in decoded
            or any(ord(character) < 32 or ord(character) == 127
                   for character in decoded)):
        raise CombRefereeScopeError(
            f"invalid render resource path: {reference}")
    logical = posixpath.normpath(
        posixpath.join(posixpath.dirname(base), decoded))
    if (logical in {"", ".", ".."} or logical.startswith("../")
            or pathlib.PurePosixPath(logical).is_absolute()):
        raise CombRefereeScopeError(
            f"render resource escapes snapshot: {reference}")
    return logical


def _stable_payload(path: pathlib.Path, logical: str) -> bytes:
    before = _stable_file_record(path, logical)
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise CombRefereeScopeError(f"cannot retain {logical}: {error}") from error
    after = _stable_file_record(path, logical)
    if (before != after or before.get("bytes") != len(payload)
            or before.get("sha256") != sha256_bytes(payload)):
        raise CombRefereeScopeError(
            f"file changed while retaining exact bytes: {logical}")
    return payload


def _gate_audit_render_dependencies(
        html_payload: bytes, entrypoint: str, html_tree: dict[str, Any],
        ) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        text = html_payload.decode("utf-8")
    except UnicodeDecodeError as error:
        return [], [f"HTML is not UTF-8: {error}"]
    scanner = GateAuditRenderDependencyScanner()
    try:
        scanner.feed(text)
        scanner.close()
    except Exception as error:  # noqa: BLE001 - malformed HTML is evidence
        return [], [f"HTML dependency scan failed: {type(error).__name__}: {error}"]
    errors = list(scanner.errors)
    pending = [(reference, entrypoint, kind)
               for reference, kind in scanner.references]
    metadata: dict[str, dict[str, Any]] = {}
    payloads: dict[str, bytes] = {}
    visited_css: set[str] = set()
    tree_files = _manifest_files(html_tree)
    while pending:
        reference, referrer, kind = pending.pop(0)
        try:
            logical = _gate_audit_logical_resource(reference, referrer)
        except CombRefereeScopeError as error:
            errors.append(f"{referrer}: {error}")
            continue
        if logical is None:
            continue
        item = metadata.setdefault(logical, {
            "path": logical, "mime_type": None, "present": False,
            "bytes": None, "sha256": None, "kinds": set(),
            "referrers": set(),
        })
        item["kinds"].add(kind)
        item["referrers"].add(referrer)
        if logical in payloads:
            continue
        candidate = BUILD / "html" / pathlib.PurePosixPath(logical)
        logical_tree_path = f"build/html/{logical}"
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to((BUILD / "html").resolve(strict=True))
            if resolved != candidate or not resolved.is_file():
                raise CombRefereeScopeError("symlinked or non-file dependency")
            payload = _stable_payload(candidate, logical_tree_path)
            expected = tree_files.get(logical_tree_path)
            if (not isinstance(expected, dict)
                    or expected.get("bytes") != len(payload)
                    or expected.get("sha256") != sha256_bytes(payload)):
                raise CombRefereeScopeError(
                    "dependency bytes differ from captured HTML tree")
        except (OSError, ValueError, CombRefereeScopeError) as error:
            errors.append(
                f"{referrer}: unresolved render dependency {reference!r} "
                f"({error})")
            continue
        payloads[logical] = payload
        mime_type = mimetypes.guess_type(logical)[0]
        if mime_type is None:
            errors.append(f"{logical}: unknown render dependency MIME type")
            continue
        item.update({
            "mime_type": mime_type, "present": True,
            "bytes": len(payload), "sha256": sha256_bytes(payload),
        })
        if logical.lower().endswith(".css") and logical not in visited_css:
            visited_css.add(logical)
            try:
                css = payload.decode("utf-8")
            except UnicodeDecodeError as error:
                errors.append(f"{logical}: CSS is not UTF-8 ({error})")
                continue
            pending.extend(
                (nested, logical, "css")
                for nested in _gate_audit_css_urls(css))
    entries = [
        {
            **{key: value for key, value in item.items()
               if key not in {"kinds", "referrers"}},
            "kinds": sorted(item["kinds"]),
            "referrers": sorted(item["referrers"]),
        }
        for _logical, item in sorted(metadata.items())
    ]
    return entries, sorted(set(errors))


def _audit_render_binding_snapshots(
        html_tree: dict[str, Any], slugs: Iterable[str],
        ) -> dict[str, Any]:
    tree_files = _manifest_files(html_tree)
    bindings: dict[str, Any] = {}
    for slug in sorted(slugs):
        logical = f"build/html/{slug}.html"
        entry = tree_files.get(logical)
        if not isinstance(entry, dict):
            raise CombRefereeScopeError(
                f"audit render entrypoint is absent from snapshot: {slug}")
        payload = _stable_payload(BUILD / "html" / f"{slug}.html", logical)
        if (entry.get("bytes") != len(payload)
                or entry.get("sha256") != sha256_bytes(payload)):
            raise CombRefereeScopeError(
                f"audit render entrypoint changed during snapshot: {slug}")
        dependencies, errors = _gate_audit_render_dependencies(
            payload, f"{slug}.html", html_tree)
        if errors:
            raise CombRefereeScopeError(
                f"audit render dependency closure is unevaluable for {slug}: "
                + "; ".join(errors[:3]))
        bindings[slug] = {
            "html_sha256": entry["sha256"],
            "entrypoint": f"{slug}.html",
            "dependencies": dependencies,
            "errors": [],
            "complete": True,
            "network_policy": (
                "deny-except-retained-relative-resources-and-inline-data"),
        }
    return bindings


def capture_audit_application_snapshot() -> dict[str, Any]:
    """Capture every application byte consumed by the isolated audit run."""
    git = _git_state()
    if not git["worktree_clean"]:
        raise CombRefereeScopeError(
            "worktree is not clean (staged, unstaged, or untracked change)")
    producers = {
        relative: _tracked_record(relative, git["commit"])
        for relative in COMB_REFEREE_PRODUCERS
    }
    if not all(record["equals_head"] for record in producers.values()):
        changed = [name for name, record in producers.items()
                   if not record["equals_head"]]
        raise CombRefereeScopeError(
            "tracked producer bytes differ from HEAD: " + ", ".join(changed))

    python_path = pathlib.Path(sys.executable).resolve()
    poppler_name = shutil.which("pdftocairo")
    if poppler_name is None:
        raise CombRefereeScopeError("pdftocairo is not installed")
    poppler_path = pathlib.Path(poppler_name).resolve()
    dependency_entries = _isolated_dependency_entries()
    runtime_library: dict[str, Any] | None = None
    library = sysconfig.get_config_var("LDLIBRARY")
    library_dir = sysconfig.get_config_var("LIBDIR")
    if library and library_dir:
        candidate = pathlib.Path(str(library_dir)) / str(library)
        if candidate.is_file():
            runtime_library = _stable_file_record(
                candidate.resolve(), "python/runtime-library")
    artifact_trees = {
        name: _tree_manifest(path, f"build/{name}")
        for name, path in COMB_REFEREE_ARTIFACT_TREES.items()
    }
    layout_bindings = _layout_binding_snapshots(
        artifact_trees["layout"], artifact_trees["guides"],
        producers["tools/formgen/lattice.py"])
    render_bindings = _audit_render_binding_snapshots(
        artifact_trees["html"], layout_bindings)
    provenance, sources = _layout_declared_inputs(
        artifact_trees["layout"], git["commit"])
    return {
        "git": git,
        "producers": producers,
        "runtime": {
            "python": _stable_file_record(python_path, str(python_path)),
            "python_identity": {
                "implementation": platform.python_implementation(),
                "version": platform.python_version(),
                "cache_tag": str(sys.implementation.cache_tag),
            },
            "python_runtime_library": runtime_library,
            "python_dependencies": _isolated_dependency_manifest(
                dependency_entries),
            "python_dependency_files": _isolated_dependency_file_projection(
                dependency_entries),
            "python_dependency_trees": _isolated_dependency_tree_projections(
                dependency_entries),
            "pymupdf_distribution_version": importlib.metadata.version(
                "pymupdf"),
            "playwright_distribution_version": importlib.metadata.version(
                "playwright"),
            "pdftocairo": _stable_file_record(poppler_path, str(poppler_path)),
        },
        "artifact_trees": artifact_trees,
        "layout_bindings": layout_bindings,
        "render_bindings": render_bindings,
        "provenance": provenance,
        "source_pdfs": sources,
    }


def capture_comb_referee_snapshot() -> dict[str, Any]:
    """Capture the complete application scope; host closure stays explicit."""
    application = capture_audit_application_snapshot()
    return {
        **application,
        "audit": _audit_snapshot(application),
    }


def snapshot_pair_errors(before: dict[str, Any],
                         after: dict[str, Any]) -> list[str]:
    """Pure validation used by the runner and its adversarial self-tests."""
    errors: list[str] = []
    for label, snapshot in (("before", before), ("after", after)):
        git = snapshot.get("git")
        if not isinstance(git, dict) or git.get("worktree_clean") is not True:
            errors.append(f"{label} worktree is dirty")
        producers = snapshot.get("producers")
        if (not isinstance(producers, dict)
                or set(producers) != set(COMB_REFEREE_PRODUCERS)
                or not all(isinstance(record, dict)
                           and record.get("equals_head") is True
                           for record in producers.values())):
            errors.append(f"{label} producer/HEAD binding is incomplete")
    before_git = before.get("git") if isinstance(before.get("git"), dict) else {}
    after_git = after.get("git") if isinstance(after.get("git"), dict) else {}
    if (before_git.get("commit") != after_git.get("commit")
            or before_git.get("tree") != after_git.get("tree")):
        errors.append("HEAD commit or tree changed during referee run")
    if before != after:
        changed = sorted(
            key for key in set(before) | set(after)
            if before.get(key) != after.get(key))
        errors.append("application snapshot changed: " + ", ".join(changed))
    return errors


SELF_DIGEST_CONTRACT = {
    "algorithm": "sha256",
    "canonicalization": "json-sort-keys-compact-utf8",
    "excluded_field": "payload_sha256",
}


def attach_self_digest(value: dict[str, Any]) -> None:
    if "self_digest" in value or "payload_sha256" in value:
        raise CombRefereeScopeError("self-digest is already attached")
    value["self_digest"] = dict(SELF_DIGEST_CONTRACT)
    value["payload_sha256"] = canonical_digest(value)


def self_digest_valid(value: dict[str, Any]) -> bool:
    claimed = value.get("payload_sha256")
    if (value.get("self_digest") != SELF_DIGEST_CONTRACT
            or not _is_sha256(claimed)):
        return False
    unsigned = {key: item for key, item in value.items()
                if key != "payload_sha256"}
    return claimed == canonical_digest(unsigned)


REPORT_KEYS = {
    "schema_version", "producer", "producer_sha256", "python_version",
    "provenance", "status", "status_reasons", "attestation", "poppler",
    "inputs", "totals", "errors", "forms", "self_digest",
    "payload_sha256",
}
REPORT_PROVENANCE_KEYS = {"producer", "dependencies", "runtime"}
REPORT_PRODUCER_KEYS = {"file", "bytes", "sha256"}
REPORT_DEPENDENCY_ROLE_KEYS = {"audit", "lattice"}
REPORT_AUDIT_DEPENDENCY_KEYS = {
    "file", "bytes", "sha256", "expected_sha256", "dependencies",
}
REPORT_PINNED_DEPENDENCY_KEYS = {
    "file", "bytes", "sha256", "expected_sha256",
}
REPORT_RUNTIME_KEYS = {
    "python_implementation", "python_version", "python_executable",
    "python_executable_sha256", "poppler",
}
REPORT_INPUT_KEYS = {"audit_sha256", "audit_bytes", "layout_count"}
REPORT_AUDIT_CHILD_DEPENDENCIES = (
    "tools/formgen/extract.py", "tools/formgen/verify.py",
)
TOTAL_KEYS = {
    "forms_expected", "forms_measured", "forms_error", "combs_expected",
    "combs_found", "combs_measured", "combs_composite", "combs_unevaluable",
    "combs_source_unevaluable", "subjects_active",
    "subjects_active_resolved", "subjects_active_unresolved",
    "subjects_retained_unresolved", "inferences_suppressed",
    "ledger_blocking", "ledger_blocking_excused",
    "referee_layout_mismatches",
    "referee_layout_position_mismatches", "comparisons", "forms_ok",
    "forms_disagreement", "forms_unevaluable",
    "audit_evidence_complete_forms", "referee_attestation_complete",
    "referee_enforceable",
}
FORM_KEYS = {
    "slug", "status", "reason", "source", "artifacts", "lattice_evidence",
    "poppler", "pages", "audit_evidence", "emission_inventory",
    "emission_binding_errors", "counts", "inferences", "cells",
}
FORM_COUNT_KEYS = {
    "combs", "subjects", "subjects_active", "subjects_active_resolved",
    "subjects_active_unresolved", "subjects_retained_unresolved",
    "inferences_suppressed", "ledger_blocking", "ledger_blocking_excused",
    "measured", "composite",
    "source_unevaluable", "unevaluable", "referee_layout_mismatches",
    "referee_layout_position_mismatches", "emission_layout_mismatches",
    "comparisons",
}
CELL_KEYS = {
    "cell", "subject_key", "legacy_cell_id", "cell_id", "ledger_state",
    "ledger_blocks_gate", "ledger_reason_codes", "ledger_topology_sha256",
    "ledger_evidence", "page", "bbox", "latticed", "lattice_divider_x",
    "emitted", "emitted_indexes_valid", "emitted_evidence", "audit_printed",
    "audit_relation", "referee", "comparison_status", "comparison_reason",
    "transition_status", "transition_reason", "four_way",
    # DECLARED SCHEMA CHANGE (C4a): every cell publishes the reviewed
    # resolution certificate that promoted it, or null.  Publishing it on
    # EVERY cell rather than only the promoted ones is deliberate: the key's
    # presence then carries no information, so a forger cannot hide a
    # promotion by omitting the field, and this gate re-derives the signed
    # four-way for any cell that carries one.
    "resolution_certificate",
    # DECLARED SCHEMA CHANGE: the transition certificate's sibling, published
    # on every cell for the same reason -- a key whose PRESENCE carries no
    # information cannot be used to hide a promotion by omission.
    "transition_certificate",
    "exception_registry_key",
}
INFERENCE_KEYS = {
    "page", "subject_key", "cell_id", "state", "blocks_gate",
    "reason_codes", "bbox", "topology_sha256", "ledger_evidence",
    "emitted_evidence",
}
AUDIT_EVIDENCE_KEYS = {
    "assertion_valid", "complete", "reason", "errors", "offender_count",
    "offenders_published", "offenders_omitted", "combs_expected",
    "combs_checked", "expected_comb_ids", "checked_comb_ids",
    "emitted_comb_ids", "unexpected_emitted_comb_ids",
    "duplicate_layout_comb_ids", "duplicate_emitted_cell_ids",
    "raw_live_comb_issues", "emitted_cell_binding_issues",
    "inventory_complete", "layout_mismatches", "layout_unevaluable",
    "owner_certificates_valid", "owner_certificates_invalid",
    "source_u_frame_evaluable", "source_certified_unframed_evaluable",
    "emission_behind_layout", "emission_invalid", "offender_dimensions",
    # Z1's declared schema change: the referee mirrors the audit's reviewed-
    # topology provenance so the gate can compare them key for key.
    "decided_by_review", "decided_by_review_subjects",
    "holds", "input_manifest_verified", "input_manifest_reason",
    "manifest_binding", "ledger_binding", "evidence_published",
    "byte_and_relation_binding_valid",
    "runtime_closure_independently_attested", "integrity_valid",
}
MANIFEST_BINDING_KEYS = {
    "binding_valid", "manifest_inputs_complete", "attestation_complete",
    "enforceable", "complete", "reason", "errors", "blockers",
    "host_scope_boundaries",
    "producer_sha256", "runtime_tree_sha256",
    "runtime_manifest_self_consistent",
    "base_runtime_closure_independently_attested",
    "roundtrip_runtime_closure_independently_attested",
    "render_dependency_count", "render_dependencies", "roundtrip_present",
}
LEDGER_BINDING_KEYS = {
    "binding_valid", "reason", "errors", "active_subject_ids",
    "emitted_ids", "legacy_alias_count",
}
EMISSION_INVENTORY_KEYS = {
    "complete", "reason", "expected_active_cell_ids", "emitted_cell_ids",
    "missing_active_cell_ids", "unexpected_emitted_cell_ids",
    "retained_emitted_cell_ids", "inference_emitted_cell_ids",
    "invalid_active_cell_ids",
}
FORM_POPPLER_KEYS = {
    "version", "binary_path", "binary_sha256", "identity_timeout_seconds",
    "page_timeout_seconds", "subprocess_cleanup_policy",
}
FORM_PAGE_KEYS = {
    "page", "svg_sha256", "vector_paints", "unsupported_regions",
}
MEASURED_REFEREE_KEYS = {
    "status", "reason", "y0", "y1", "source_divider_x", "source_rail_x",
    "extra_divider_x", "compartments", "anchor_matches",
    "positions_match", "anchors_complete", "subject_gap_proofs",
    "unproven_subject_gaps", "components", "contract_y0", "contract_y1",
    "open_y0", "open_y1", "contract_span_pt", "seed_span_pt",
    "measured_span_pt", "unmeasured_span_pt", "topology_coverage_pt",
    "ignored_slabs", "chosen_topology", "topology_superset_relations",
    # DECLARED SCHEMA CHANGE (R1, F232): every measured band now names, per
    # side, the basis that placed its rail -- "owner-edge",
    # "wall-outside-run", or "prose-refuted-outer-region".  Before this key a
    # rail strictly inside the rectangle was indistinguishable from a rail at
    # its edge, so the gate could not re-derive WHY a rail sat where it did;
    # _rail_derivation_errors now cross-checks each basis against the
    # published topology, which is strictly more than it could check before.
    "rail_derivation",
}
RAIL_DERIVATION_BASES = {
    "owner-edge", "wall-outside-run", "prose-refuted-outer-region",
}
# The referee's third measured-source shape: the fail-closed partial-anchor
# certificate (comb_referee.ACTIVE_PARTIAL_ANCHOR_CRITERION).  It deliberately
# omits subject_gap_proofs/unproven_subject_gaps — those proofs are only
# computed on the full-anchor path, and publishing empty lists here would
# assert "no unproven gaps" for a test that never ran — and instead carries
# the erased-anchor inventory plus the erasure certificate the gate
# independently re-derives in _partial_anchor_referee_certificate_errors.
# DECLARED SCHEMA (C3-A): a reviewed composite subject has no comb, so it
# publishes no band measurement.  Its certificate is the source corroboration
# of the suppression the review confirmed, and these are its exact keys.
COMPOSITE_REFEREE_KEYS = {"status", "criterion", "corroborated", "reason"}
COMPOSITE_SUPPRESSION_CRITERIA = {
    # The exact strings comb_referee.py tables; the first was declared here
    # from memory, wrong ("source-caption-block-..."), and the schema guard
    # rejected the real certificate on 1606 p2c135 -- fixed to the real one.
    "source-printed-caption-block-not-character-cells-v1",
    "source-partition-edge-in-final-picture-v1",
    "source-crossing-rule-not-comb-scoped-v1",
}
PARTIAL_ANCHOR_REFEREE_KEYS = (
    MEASURED_REFEREE_KEYS - {"subject_gap_proofs", "unproven_subject_gaps"}
) | {"missing_anchor_x", "active_partial_anchor_certificate"}
PARTIAL_ANCHOR_CRITERION = (
    "active-full-band-partial-anchor-source-topology-v1"
)
PARTIAL_ANCHOR_CERTIFICATE_KEYS = {
    "criterion", "valid", "ledger_state", "subject_ownership_basis",
    "independent_source_enclosure_proven", "divider_count_basis",
    "missing_anchor_basis", "anchor_corridor_clipped_paint_elements",
    "anchor_corridor_unsupported_region_elements", "open_y0", "open_y1",
    "coverage_pt", "source_divider_x", "observed_anchor_x",
    "missing_anchor_x", "missing_anchor_proofs",
}
PARTIAL_ANCHOR_PROOF_KEYS = {
    "layout_x", "corridor_x0", "corridor_x1", "proof_x0", "proof_x1",
    "open_y0", "open_y1", "raw_anchor_rails", "raw_rail_identity_valid",
    "proof_top_role_ambiguities", "erasure_slabs", "erasure_owner_roles",
    "clipped_paint_elements", "final_target_tone_segments",
    "unsupported_region_elements",
}
PARTIAL_ANCHOR_RAIL_KEYS = {
    "element", "order", "kind", "x0", "x1", "center_x", "delta_pt",
    "y0", "y1", "tone", "clipped",
}
PARTIAL_ANCHOR_SLAB_KEYS = {
    "y0", "y1", "sample_y", "raw_rail_elements", "raw_intervals",
    "final_owner_segments", "ambiguous_top_roles",
}
PARTIAL_ANCHOR_SEGMENT_KEYS = {
    "x0", "x1", "element", "order", "kind", "tone", "clipped",
}
PARTIAL_ANCHOR_ROLE_KEYS = {"element", "order", "kind", "tone"}
PARTIAL_ANCHOR_OWNERSHIP_BASIS = "active_unresolved lattice ledger"
PARTIAL_ANCHOR_COUNT_BASIS = "final-composited Poppler vector topology"
PARTIAL_ANCHOR_MISSING_BASIS = (
    "raw target-tone rail exhaustively replaced by one supported "
    "unclipped non-target final owner"
)
LEDGER_STATES = {
    "active_resolved", "active_unresolved", "active_composite",
    "retained_unresolved",
}
# Declared ONCE and consumed everywhere, because this contract has now been
# discovered three separate times in three files by watching it fail. A
# SUPPRESSED subject has no active cell of its own and emits nothing; a
# NON-BLOCKING one does not hold the gate. A reviewed composite is both,
# which is exactly what makes it easy to miss in a test written against
# either property alone.
LEDGER_SUPPRESSED_STATES = {"retained_unresolved", "active_composite"}
LEDGER_NONBLOCKING_STATES = {"active_resolved", "active_composite"}
INFERENCE_STATE = "suppressed_unreviewed_inference"
RAW_REFEREE_ATTESTATION_KEYS = {
    "schema", "producer_and_declared_dependency_bytes_bound",
    "published_form_input_bytes_bound_before_after",
    "python_executable_fingerprinted",
    "python_executable_validated_before_after",
    "poppler_executable_bound_before_after",
    "poppler_invocations_have_hard_deadlines",
    "poppler_timeout_cleanup_policy", "clean_source_revision_bound",
    "python_stdlib_closure_bound", "python_dynamic_libraries_bound",
    "poppler_dynamic_libraries_bound",
    "operating_system_and_host_services_bound", "scope_complete",
    "complete", "enforceable", "incomplete_reasons",
    "future_gate_required",
}
RAW_REFEREE_INCOMPLETE_REASONS = [
    (
        "the standalone referee hashes its source and declared local "
        "dependencies but is not bound to a reviewed clean source revision"
    ),
    (
        "the Python standard library, Python dynamic libraries, Poppler "
        "dynamic libraries, and operating-system services are outside the "
        "independently rehashed application closure"
    ),
    (
        "the Python executable is fingerprinted for reporting but is not "
        "independently snapshotted and revalidated before and after the run"
    ),
]
RAW_REFEREE_FUTURE_GATE = (
    "trusted clean-source and host/runtime closure binding")
# Empty because the referee now closes what these blockers described, not
# because the strings were deleted to make a check pass. Until 2026-08-10 this
# list carried seven entries, and every audit binding carried all seven:
#
#   audit producer/runtime attestation is incomplete
#   audit evidence is not yet enforceable
#   audit input manifest is intentionally non-gating
#   audit base runtime scope is incomplete
#   audit PyMuPDF/application runtime closure is manifest-self-consistent
#     only; the referee independently rehashes the Python executable but not
#     every named module or native dependency
#   audit roundtrip native runtime scope is incomplete
#   audit Playwright/Chromium closure is manifest-schema checked but not
#     independently rehashed by the standalone referee
#
# comb_referee.verify_published_closure and
# comb_referee.verify_published_roundtrip_closure now resolve every named
# module, every bundled native library and the whole Playwright/Chromium tree
# from the installed package and rehash them, so the referee reaches a verdict
# instead of refusing to. Each blocker string still exists in comb_referee.py
# and reappears here the moment its own condition holds again -- a failed
# rehash, a withheld round trip, an audit that declines to claim what was
# verified. What the closure never covered (the Python standard library, the
# interpreter's dynamic libraries and the operating system's own) is published
# per binding under `host_scope_boundaries` and is the host TCB this gate
# already declares in its audit application envelope.
RAW_AUDIT_SCOPE_BLOCKERS: list[str] = []


def _raw_referee_attestation_errors(value: Any) -> list[str]:
    if not isinstance(value, dict) or set(value) != RAW_REFEREE_ATTESTATION_KEYS:
        return ["report raw attestation schema is unsupported"]
    expected = {
        "schema": "comb-referee-runtime-attestation-v1",
        "producer_and_declared_dependency_bytes_bound": True,
        "published_form_input_bytes_bound_before_after": True,
        "python_executable_fingerprinted": True,
        "python_executable_validated_before_after": False,
        "poppler_executable_bound_before_after": True,
        "poppler_invocations_have_hard_deadlines": True,
        "poppler_timeout_cleanup_policy": "kill-isolated-process-group",
        "clean_source_revision_bound": False,
        "python_stdlib_closure_bound": False,
        "python_dynamic_libraries_bound": False,
        "poppler_dynamic_libraries_bound": False,
        "operating_system_and_host_services_bound": False,
        "scope_complete": False,
        "complete": False,
        "enforceable": False,
    }
    errors = [
        f"report raw attestation relation is false: {key}"
        for key, expected_value in expected.items()
        if value.get(key) != expected_value
    ]
    if value.get("incomplete_reasons") != RAW_REFEREE_INCOMPLETE_REASONS:
        errors.append("report raw attestation reasons are not the exact contract")
    if value.get("future_gate_required") != RAW_REFEREE_FUTURE_GATE:
        errors.append("report raw attestation future gate is not the exact contract")
    return errors


def _transition_for_cell(ledger_state: str, comparison_status: str
                         ) -> tuple[str, str]:
    if comparison_status == "excepted":
        return (
            "none",
            "reviewed exception holds; the paper cannot adjudicate a "
            "transition",
        )
    if ledger_state == "active_resolved":
        return "none", "active ledger subject is already resolved"
    if ledger_state == "active_composite":
        return "none", "reviewed composite transition is already applied"
    if ledger_state == "active_unresolved":
        if comparison_status == "agree":
            return (
                "eligible-for-reviewed-resolution",
                "four-way evidence agrees; explicit review is still required",
            )
        return (
            "blocked",
            "active unresolved ledger subject remains blocking while "
            f"comparison status is {comparison_status}",
        )
    if ledger_state == "retained_unresolved":
        return (
            "explicit-transition-required",
            "retained unresolved subject has no active topology; an explicit "
            "ledger transition is required",
        )
    return "invalid", "invalid ledger state"


def _finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return True if isinstance(value, int) else math.isfinite(value)


_PERCENTAGE_EVIDENCE = {
    "rules_pct": ("rules_ref", "rules_missing"),
    "text_pct": ("text_ref", "text_missing"),
}


def _percentage_evidence_errors(
        record: dict[str, Any], pct_key: str, label: str,
        ) -> list[str]:
    """Bind a published percentage to its positive source denominator."""
    relation = _PERCENTAGE_EVIDENCE.get(pct_key)
    if relation is None:
        return [f"{label}: unsupported percentage metric {pct_key}"]
    reference_key, missing_key = relation
    reference = record.get(reference_key)
    missing = record.get(missing_key)
    percentage = record.get(pct_key)
    errors: list[str] = []
    if not _is_count(reference) or reference == 0:
        errors.append(
            f"{label}: {reference_key} is absent or not a positive int")
    if not _is_count(missing):
        errors.append(
            f"{label}: {missing_key} is absent or not a nonnegative int")
    if (not _finite_number(percentage)
            or not 0 <= percentage <= 100):
        errors.append(
            f"{label}: {pct_key} is absent or not finite in 0..100")
    if not errors:
        if missing > reference:
            errors.append(
                f"{label}: {pct_key} is not derived from "
                f"{reference_key} and {missing_key}")
        else:
            # Match audit.py's producer expression exactly.  Re-idealising
            # this as an exact Decimal ratio rejects honest reports at binary
            # floating-point half-cent boundaries (for example 4000/1).
            try:
                expected = round(
                    100.0 * (reference - missing) / reference, 2)
            except (OverflowError, ZeroDivisionError, ValueError):
                # A producer-shaped JSON integer can be larger than a binary
                # float even though its percentage is still well-defined.
                # Keep that evidence evaluable with the exact fallback; the
                # normal path above remains byte-for-byte audit.py's formula.
                try:
                    with localcontext() as context:
                        context.prec = max(
                            reference.bit_length(), missing.bit_length(), 64) + 16
                        expected_decimal = (
                            Decimal(100)
                            * (Decimal(reference) - Decimal(missing))
                            / Decimal(reference)
                        ).quantize(
                            Decimal("0.01"), rounding=ROUND_HALF_EVEN)
                except (InvalidOperation, ValueError, ZeroDivisionError):
                    errors.append(
                        f"{label}: {pct_key} cannot be re-derived from "
                        f"{reference_key} and {missing_key}")
                else:
                    if Decimal(str(percentage)) != expected_decimal:
                        errors.append(
                            f"{label}: {pct_key} is not derived from "
                            f"{reference_key} and {missing_key}")
            else:
                observed = Decimal(str(percentage))
                if observed != Decimal(str(expected)):
                    errors.append(
                        f"{label}: {pct_key} is not derived from "
                        f"{reference_key} and {missing_key}")
    return errors


def _finite_number_list(value: Any, *, length: int | None = None) -> bool:
    return (isinstance(value, list)
            and (length is None or len(value) == length)
            and all(_finite_number(item) for item in value))


def _comparison_for_cell(
        cell: dict[str, Any], audit_complete: bool,
        ) -> tuple[str, str]:
    """Mirror comb_referee.comparison; published labels are never trusted."""
    ledger_state = cell.get("ledger_state")
    if ledger_state == "active_composite":
        # Mirror of comb_referee.composite_comparison.  A reviewed composite
        # has no comb, so it is scored on the source corroboration of its
        # suppression -- and a corroboration that came back FALSE stops the
        # gate, because review can never overrule the paper.
        referee = cell.get("referee") or {}
        if cell.get("emitted") is not None:
            return (
                "stop",
                "a composite subject emitted physical slots of its own",
            )
        if referee.get("status") != "composite":
            return (
                "unevaluable",
                "composite subject carries no corroboration measurement",
            )
        if not referee.get("corroborated"):
            return (
                "stop",
                "the source refutes the reviewed composite's suppression "
                "claim",
            )
        return (
            "agree",
            "the source corroborates the reviewed composite's suppression "
            "claim",
        )
    if ledger_state not in {"active_resolved", "active_unresolved"}:
        return (
            "unevaluable",
            "ledger subject has no active topology for adjudication",
        )
    lattice = cell.get("latticed")
    emitted = cell.get("emitted")
    referee = cell.get("referee")
    if emitted != lattice or cell.get("emitted_indexes_valid") is not True:
        return "stale-generation", "emitted physical slots disagree with lattice"
    if not audit_complete:
        return "unevaluable", "audit evidence is incomplete"
    if cell.get("audit_printed") is None:
        return (
            "unevaluable",
            "audit published this subject as an offender with no printed "
            "topology",
        )
    certificate = cell.get("resolution_certificate")
    if isinstance(certificate, dict):
        # Mirror of comb_referee.comparison's review guard: a signed
        # resolution is re-derived against THIS run's four measurements, and
        # a corpus that moved under it stops the gate.  Review can never
        # overrule the paper, and a stale review is not a pass.
        signed = certificate.get("four_way")
        if (not isinstance(signed, dict)
                or set(signed) != {"lattice", "audit", "emitted", "referee"}):
            return "stop", "resolution certificate publishes no four-way"
        measured_referee = (
            referee.get("compartments") if isinstance(referee, dict) else None)
        if (signed.get("lattice") != lattice
                or signed.get("audit") != cell.get("audit_printed")
                or signed.get("emitted") != emitted
                or (isinstance(referee, dict)
                    and referee.get("status") == "measured"
                    and signed.get("referee") != measured_referee)):
            return (
                "stop",
                "the evidence has moved since this resolution was reviewed",
            )
    if not isinstance(referee, dict) or referee.get("status") != "measured":
        reason = referee.get("reason", "no reason") if isinstance(
            referee, dict) else "no reason"
        return "unevaluable", f"referee: {reason}"
    if referee.get("positions_match") is not True:
        return "stop", "referee positions disagree with lattice anchors"
    source = referee.get("compartments")
    audit = cell.get("audit_printed")
    if not _is_count(source) or not _is_count(lattice) or not _is_count(audit):
        raise ValueError("comparison operands are not exact nonnegative integers")
    if source == lattice == audit:
        return "agree", "referee, lattice, audit, and emitted agree"
    if source == audit and source != lattice:
        return "repair-lattice", "referee and audit agree against lattice"
    if source == lattice and source != audit:
        return "repair-audit", "referee and lattice agree against audit"
    if lattice == audit and source != lattice:
        return "stop", "lattice and audit agree against the independent referee"
    return "stop", "referee, lattice, and audit all differ"


def _form_status_relation(
        *, ledger_blocking: int, emission_inventory: dict[str, Any],
        audit_evidence: dict[str, Any], comparisons: dict[str, int],
        ledger_blocking_excused: int = 0,
        ) -> tuple[str, str]:
    """Mirror the producer's ordered form status/reason relation exactly."""
    status = "ok"
    reasons: list[str] = []
    # An excused blocker -- one whose cell's comparison is a re-derived
    # reviewed exception -- is counted out loud but does not make the form
    # unevaluable; only unexcused blockers do. Mirrors the producer's
    # excusal exactly, including the reason strings.
    blocking_unexcused = ledger_blocking - ledger_blocking_excused
    if blocking_unexcused:
        status = "unevaluable"
        reasons.append(f"{blocking_unexcused} lattice-ledger blockers")
    elif ledger_blocking_excused:
        reasons.append(
            f"{ledger_blocking_excused} blocker(s) excused by reviewed "
            "exception")
    if emission_inventory.get("complete") is not True:
        status = "unevaluable"
        reasons.append(
            "emission inventory incomplete: "
            f"{emission_inventory.get('reason')}")
    if audit_evidence.get("complete") is not True:
        status = "unevaluable"
        reasons.append(
            f"audit evidence incomplete: {audit_evidence.get('reason')}")
    if comparisons["unevaluable"]:
        status = "unevaluable"
        reasons.append(f"{comparisons['unevaluable']} combs unevaluable")
    if status != "unevaluable" and any(
            comparisons[name] for name in (
                "repair-lattice", "repair-audit", "stale-generation", "stop")):
        status = "disagreement"
        reasons.append("one or more four-way comparisons disagree")
    return status, ", ".join(reasons) if reasons else "all combs measured"


def _string_list(value: Any, *, nonempty: bool = False) -> bool:
    return (isinstance(value, list)
            and (not nonempty or bool(value))
            and all(isinstance(item, str) and item for item in value))


def _report_poppler_identity_valid(value: Any) -> bool:
    return bool(
        isinstance(value, dict)
        and set(value) == FORM_POPPLER_KEYS
        and isinstance(value.get("version"), str) and value["version"]
        and isinstance(value.get("binary_path"), str)
        and value["binary_path"]
        and _is_sha256(value.get("binary_sha256"))
        and value.get("identity_timeout_seconds") == 10.0
        and value.get("page_timeout_seconds") == 60.0
        and value.get("subprocess_cleanup_policy")
        == "kill-isolated-process-group"
    )


def _report_provenance_schema_errors(report: dict[str, Any]) -> list[str]:
    """Validate the child's exact declared producer/runtime closure."""
    errors: list[str] = []
    provenance = report.get("provenance")
    if not isinstance(provenance, dict) or set(provenance) != REPORT_PROVENANCE_KEYS:
        return ["report provenance schema is unsupported"]
    producer = provenance.get("producer")
    if (not isinstance(producer, dict)
            or set(producer) != REPORT_PRODUCER_KEYS
            or producer.get("file") != "tools/formgen/comb_referee.py"
            or not _is_count(producer.get("bytes"))
            or not _is_sha256(producer.get("sha256"))
            or producer.get("sha256") != report.get("producer_sha256")):
        errors.append("report producer provenance is malformed")

    dependencies = provenance.get("dependencies")
    if (not isinstance(dependencies, dict)
            or set(dependencies) != REPORT_DEPENDENCY_ROLE_KEYS):
        errors.append("report dependency-role inventory is not exact")
        dependencies = {}
    audit = dependencies.get("audit")
    if (not isinstance(audit, dict)
            or set(audit) != REPORT_AUDIT_DEPENDENCY_KEYS
            or audit.get("file") != "tools/formgen/audit.py"
            or not _is_count(audit.get("bytes"))
            or not _is_sha256(audit.get("sha256"))
            or audit.get("expected_sha256") != audit.get("sha256")):
        errors.append("report audit dependency provenance is malformed")
        audit = {}
    audit_children = audit.get("dependencies")
    if not isinstance(audit_children, list):
        errors.append("report audit child-dependency inventory is malformed")
        audit_children = []
    child_files = [
        item.get("file") if isinstance(item, dict) else None
        for item in audit_children
    ]
    if child_files != list(REPORT_AUDIT_CHILD_DEPENDENCIES):
        errors.append("report audit child-dependency inventory is not exact")
    for expected_file, child in zip(
            REPORT_AUDIT_CHILD_DEPENDENCIES, audit_children):
        if (not isinstance(child, dict)
                or set(child) != REPORT_PINNED_DEPENDENCY_KEYS
                or child.get("file") != expected_file
                or not _is_count(child.get("bytes"))
                or not _is_sha256(child.get("sha256"))
                or child.get("expected_sha256") != child.get("sha256")):
            errors.append(
                f"report audit child dependency is malformed: {expected_file}")

    lattice = dependencies.get("lattice")
    if (not isinstance(lattice, dict)
            or set(lattice) != REPORT_PINNED_DEPENDENCY_KEYS
            or lattice.get("file") != "tools/formgen/lattice.py"
            or not _is_count(lattice.get("bytes"))
            or not _is_sha256(lattice.get("sha256"))
            or lattice.get("expected_sha256") != lattice.get("sha256")):
        errors.append("report lattice dependency provenance is malformed")

    runtime = provenance.get("runtime")
    if (not isinstance(runtime, dict) or set(runtime) != REPORT_RUNTIME_KEYS):
        errors.append("report runtime provenance schema is unsupported")
        runtime = {}
    if (not isinstance(runtime.get("python_implementation"), str)
            or not runtime.get("python_implementation")
            or not isinstance(runtime.get("python_version"), str)
            or not runtime.get("python_version")
            or runtime.get("python_version") != report.get("python_version")
            or not isinstance(runtime.get("python_executable"), str)
            or not runtime.get("python_executable")
            or not _is_sha256(runtime.get("python_executable_sha256"))):
        errors.append("report Python runtime provenance is malformed")
    poppler = report.get("poppler")
    if (not _report_poppler_identity_valid(poppler)
            or runtime.get("poppler") != poppler):
        errors.append("report Poppler runtime provenance is malformed")

    inputs = report.get("inputs")
    if (not isinstance(inputs, dict) or set(inputs) != REPORT_INPUT_KEYS
            or not _is_sha256(inputs.get("audit_sha256"))
            or not _is_count(inputs.get("audit_bytes"))
            or not _is_count(inputs.get("layout_count"))):
        errors.append("report input identity schema is unsupported")
    return errors


def validate_comb_referee_report(
        report: Any, *, child_exit: int | None = None,
        expected_forms: int = EXPECTED_FORMS,
        expected_subjects: int = EXPECTED_COMB_SUBJECTS,
        ) -> tuple[list[str], dict[str, Any]]:
    """Validate v2 shape, digest, and internally recomputed corpus totals."""
    errors: list[str] = []
    stats: dict[str, Any] = {
        "pending_transitions": 0,
        "referee_layout_mismatches": 0,
        "referee_layout_position_mismatches": 0,
        "emission_layout_mismatches": 0,
        "application_status": "unevaluable",
    }
    if not isinstance(report, dict):
        return ["report is not an object"], stats
    if set(report) != REPORT_KEYS:
        errors.append("report top-level schema is incomplete or unsupported")
    if report.get("schema_version") != COMB_REFEREE_REPORT_VERSION:
        errors.append("report schema version is not supported")
    if report.get("producer") != "tools/formgen/comb_referee.py":
        errors.append("report producer identity is invalid")
    if not _is_sha256(report.get("producer_sha256")):
        errors.append("report producer digest is invalid")
    if not self_digest_valid(report):
        errors.append("report self-digest is missing or stale")
    status = report.get("status")
    if status not in {"ok", "disagreement", "unevaluable"}:
        errors.append("report status is invalid")
    reasons = report.get("status_reasons")
    if (not isinstance(reasons, list)
            or not all(isinstance(reason, str) and reason for reason in reasons)):
        errors.append("report status reasons are malformed")
    attestation = report.get("attestation")
    errors.extend(_raw_referee_attestation_errors(attestation))
    if not isinstance(attestation, dict):
        attestation = {}
    if not isinstance(report.get("python_version"), str) or not report.get(
            "python_version"):
        errors.append("report Python version is malformed")
    errors.extend(_report_provenance_schema_errors(report))

    expected_exit = {"ok": 0, "disagreement": 1, "unevaluable": 2}.get(status)
    if child_exit is not None and child_exit != expected_exit:
        errors.append("child exit code disagrees with report status")

    totals = report.get("totals")
    if not isinstance(totals, dict) or set(totals) != TOTAL_KEYS:
        drift = (sorted(set(totals) ^ TOTAL_KEYS)
                 if isinstance(totals, dict) else ["not-a-dict"])
        errors.append(
            "report totals schema is incomplete or unsupported: "
            + ", ".join(drift[:6]))
        totals = {}
    for key in TOTAL_KEYS - {
            "comparisons", "referee_attestation_complete",
            "referee_enforceable"}:
        if not _is_count(totals.get(key)):
            errors.append(f"report total is invalid: {key}")
    for key in ("referee_attestation_complete", "referee_enforceable"):
        if not isinstance(totals.get(key), bool):
            errors.append(f"report total is not boolean: {key}")
    comparisons = totals.get("comparisons")
    if (not isinstance(comparisons, dict)
            or set(comparisons) != set(COMPARISON_NAMES)
            or not all(_is_count(comparisons.get(name))
                       for name in COMPARISON_NAMES)):
        errors.append("report comparison totals are malformed")
        comparisons = {name: 0 for name in COMPARISON_NAMES}

    raw_errors = report.get("errors")
    if (not isinstance(raw_errors, list)
            or not all(isinstance(item, dict)
                       and isinstance(item.get("slug"), str)
                       and isinstance(item.get("error"), str)
                       for item in raw_errors)):
        errors.append("report error inventory is malformed")
        raw_errors = []
    forms = report.get("forms")
    if not isinstance(forms, list):
        errors.append("report forms inventory is missing")
        forms = []
    if len(forms) != expected_forms:
        errors.append(
            f"report is partial: {len(forms)}/{expected_forms} forms")

    # Corpus-scoped: every reviewed registry entry applied exactly once,
    # across ALL documents. See `_reviewed_registry_coverage_errors` -- the
    # per-document half of this guard cannot be completed from one layout.
    #
    # Only asked of a COMPLETE report. On a partial one every absent form's
    # entries look "applied nowhere", which is true of the report and false
    # of the corpus -- it would bury the real fault (the partiality, already
    # reported above) under a list of its consequences. Skipping is safe
    # precisely because a partial report can never reach PASS.
    if len(forms) == expected_forms:
        errors.extend(_reviewed_registry_coverage_errors(
            cell for form in forms if isinstance(form, dict)
            for cell in (form.get("cells") or [])
            if isinstance(cell, dict)))

    slugs: set[str] = set()
    corpus_cell_ids: set[str] = set()
    corpus_subject_keys: set[str] = set()
    recomputed = {
        "combs": 0, "measured": 0, "composite": 0, "source_unevaluable": 0,
        "unevaluable": 0, "ledger_blocking": 0,
        "ledger_blocking_excused": 0,
        "subjects_active": 0, "subjects_active_resolved": 0,
        "subjects_active_unresolved": 0,
        "subjects_retained_unresolved": 0, "inferences_suppressed": 0,
        "referee_layout_mismatches": 0,
        "referee_layout_position_mismatches": 0,
        "emission_layout_mismatches": 0,
        "audit_evidence_complete_forms": 0,
        **{f"comparison:{name}": 0 for name in COMPARISON_NAMES},
        "forms_ok": 0, "forms_disagreement": 0, "forms_unevaluable": 0,
    }
    for form in forms:
        if not isinstance(form, dict) or set(form) != FORM_KEYS:
            errors.append("form report schema is incomplete or unsupported")
            continue
        slug = form.get("slug")
        if not isinstance(slug, str) or not slug or slug in slugs:
            errors.append("form report has a missing or duplicate slug")
        else:
            slugs.add(slug)
        form_status = form.get("status")
        if form_status not in {"ok", "disagreement", "unevaluable"}:
            errors.append(f"form status is invalid: {slug}")
        if not isinstance(form.get("reason"), str) or not form["reason"]:
            errors.append(f"form reason is malformed: {slug}")
        counts = form.get("counts")
        cells = form.get("cells")
        inferences = form.get("inferences")
        if (not isinstance(counts, dict) or not isinstance(cells, list)
                or not isinstance(inferences, list)):
            errors.append(f"form counts/cells/inferences are malformed: {slug}")
            continue
        if set(counts) != FORM_COUNT_KEYS:
            errors.append(f"form totals schema is incomplete or unsupported: {slug}")
        for key in FORM_COUNT_KEYS - {"comparisons"}:
            if not _is_count(counts.get(key)):
                errors.append(f"form total is invalid: {slug}/{key}")
        form_comparisons = counts.get("comparisons")
        if (not isinstance(form_comparisons, dict)
                or set(form_comparisons) != set(COMPARISON_NAMES)
                or not all(_is_count(form_comparisons.get(name))
                           for name in COMPARISON_NAMES)):
            errors.append(f"form comparison totals are invalid: {slug}")
            form_comparisons = {name: 0 for name in COMPARISON_NAMES}
        if counts.get("combs") != len(cells):
            errors.append(f"form cell inventory is partial: {slug}")
        audit_evidence = form.get("audit_evidence")
        emission_inventory = form.get("emission_inventory")
        if (not isinstance(audit_evidence, dict)
                or set(audit_evidence) != AUDIT_EVIDENCE_KEYS
                or not isinstance(audit_evidence.get("complete"), bool)
                or not isinstance(audit_evidence.get("reason"), str)
                or not audit_evidence.get("reason")):
            errors.append(f"form audit evidence schema is malformed: {slug}")
            audit_evidence = {"complete": False, "reason": "invalid"}
        else:
            if (audit_evidence["complete"] is True
                    and audit_evidence["reason"] != "complete"):
                errors.append(f"form audit-complete reason is false: {slug}")
            source_u_frame = audit_evidence.get("source_u_frame_evaluable")
            source_unframed = audit_evidence.get(
                "source_certified_unframed_evaluable")
            checked_count = audit_evidence.get("combs_checked")
            expected_ids = audit_evidence.get("expected_comb_ids")
            checked_ids = audit_evidence.get("checked_comb_ids")
            offender_dimensions = audit_evidence.get("offender_dimensions")
            # DECLARED SCHEMA CHANGE (Z1): mirrors the three-way partition in
            # _normalise_outer_comb_assertion.  Reviewed-topology cells are
            # evaluable by decision, are not offenders, and are covered by
            # neither source term.
            reviewed_n = audit_evidence.get("decided_by_review")
            reviewed_list = audit_evidence.get("decided_by_review_subjects")
            reviewed_ids = [
                subject.get("cell") for subject in reviewed_list
                if isinstance(subject, dict)] if isinstance(
                    reviewed_list, list) else []
            source_accounting_malformed = bool(
                not _is_count(source_u_frame)
                or not _is_count(source_unframed)
                or not _is_count(checked_count)
                or not isinstance(expected_ids, list)
                or not all(isinstance(item, str) and item
                           for item in (expected_ids or []))
                or len(expected_ids or []) != len(set(expected_ids or []))
                or checked_ids != expected_ids
                or checked_count != len(expected_ids or [])
                or not isinstance(offender_dimensions, dict)
                or not _is_count(reviewed_n)
                or not isinstance(reviewed_list, list)
                or len(reviewed_list) != reviewed_n
                or len(reviewed_ids) != reviewed_n
                or not all(isinstance(cell, str) and cell
                           for cell in reviewed_ids)
                or len(set(reviewed_ids)) != len(reviewed_ids)
                or any(cell not in set(expected_ids or [])
                       for cell in reviewed_ids)
            )
            checked_source_unevaluable: set[str] = set()
            if not source_accounting_malformed:
                for cell_id in expected_ids:
                    offender = offender_dimensions.get(cell_id)
                    if offender is None:
                        continue
                    dimensions = (offender.get("dimensions")
                                  if isinstance(offender, dict) else None)
                    source_unevaluable = (
                        dimensions.get("source_unevaluable")
                        if isinstance(dimensions, dict) else None)
                    if not isinstance(source_unevaluable, bool):
                        source_accounting_malformed = True
                        break
                    if source_unevaluable:
                        checked_source_unevaluable.add(cell_id)
            if source_accounting_malformed:
                errors.append(
                    f"form audit source accounting is malformed: {slug}")
            elif set(reviewed_ids) & checked_source_unevaluable:
                errors.append(
                    f"form audit counts a reviewed cell as "
                    f"source-unevaluable: {slug}")
            elif (source_u_frame + source_unframed + len(reviewed_ids)
                  != checked_count - len(checked_source_unevaluable)):
                errors.append(
                    f"form audit source frame/unframed/reviewed partition "
                    f"is false: {slug}")
        if (not isinstance(emission_inventory, dict)
                or set(emission_inventory) != EMISSION_INVENTORY_KEYS
                or not isinstance(emission_inventory.get("complete"), bool)
                or not isinstance(emission_inventory.get("reason"), str)
                or not emission_inventory.get("reason")):
            errors.append(f"form emission inventory is malformed: {slug}")
            emission_inventory = {"complete": False, "reason": "invalid"}
        elif (emission_inventory["complete"] is True
              and emission_inventory["reason"] != "complete"):
            errors.append(f"form emission-complete reason is false: {slug}")
        if (not isinstance(form.get("emission_binding_errors"), list)
                or form.get("emission_binding_errors")):
            errors.append(f"form emission binding has errors: {slug}")
        manifest_binding = audit_evidence.get("manifest_binding")
        if (not isinstance(manifest_binding, dict)
                or set(manifest_binding) != MANIFEST_BINDING_KEYS
                or manifest_binding.get("binding_valid") is not True
                or manifest_binding.get("manifest_inputs_complete") is not True
                or manifest_binding.get("runtime_manifest_self_consistent")
                is not True
                or manifest_binding.get("errors") != []
                or not isinstance(manifest_binding.get("blockers"), list)
                or not all(isinstance(item, str) and item
                           for item in manifest_binding.get("blockers", []))):
            errors.append(f"form audit manifest binding is not clean: {slug}")
        ledger_binding = audit_evidence.get("ledger_binding")
        if (not isinstance(ledger_binding, dict)
                or set(ledger_binding) != LEDGER_BINDING_KEYS
                or ledger_binding.get("binding_valid") is not True
                or ledger_binding.get("reason") != "complete"
                or ledger_binding.get("errors") != []):
            errors.append(f"form audit ledger binding is not clean: {slug}")
        if (audit_evidence.get("assertion_valid") is not True
                or audit_evidence.get("errors") != []
                or audit_evidence.get("input_manifest_verified") is not True
                or audit_evidence.get("evidence_published") is not True
                or audit_evidence.get("byte_and_relation_binding_valid")
                is not True):
            errors.append(f"form audit relation contains errors: {slug}")
        form_poppler = form.get("poppler")
        if (not isinstance(form_poppler, dict)
                or set(form_poppler) != FORM_POPPLER_KEYS
                or not isinstance(form_poppler.get("version"), str)
                or not form_poppler.get("version")
                or not _is_sha256(form_poppler.get("binary_sha256"))
                or form_poppler.get("identity_timeout_seconds") != 10.0
                or form_poppler.get("page_timeout_seconds") != 60.0
                or form_poppler.get("subprocess_cleanup_policy")
                != "kill-isolated-process-group"):
            errors.append(f"form Poppler evidence is malformed: {slug}")
        pages = form.get("pages")
        source = form.get("source")
        expected_page_count = source.get("page_count") if isinstance(
            source, dict) else None
        if (not isinstance(pages, list)
                or len(pages) != expected_page_count):
            errors.append(f"form page evidence is incomplete: {slug}")
        else:
            for expected_page, page_record in enumerate(pages, 1):
                if (not isinstance(page_record, dict)
                        or set(page_record) != FORM_PAGE_KEYS
                        or page_record.get("page") != expected_page
                        or not _is_sha256(page_record.get("svg_sha256"))
                        or not _is_count(page_record.get("vector_paints"))
                        or not _is_count(page_record.get(
                            "unsupported_regions"))):
                    errors.append(
                        f"form page evidence is malformed: "
                        f"{slug}/p{expected_page}")
        if audit_evidence["complete"]:
            recomputed["audit_evidence_complete_forms"] += 1
        cell_comparisons = {name: 0 for name in COMPARISON_NAMES}
        state_counts = {state: 0 for state in LEDGER_STATES}
        measured_cells = source_unevaluable_cells = pending = 0
        composite_cells = 0
        blocking_cells = layout_mismatches = position_mismatches = 0
        excused_blocking_cells = 0
        emission_mismatches = 0
        form_cell_ids: set[str] = set()
        form_legacy_ids: set[str] = set()
        form_subject_keys: set[str] = set()
        for cell in cells:
            if not isinstance(cell, dict) or set(cell) != CELL_KEYS:
                errors.append(f"form has malformed cell evidence: {slug}")
                continue
            published_id = cell.get("cell")
            legacy_id = cell.get("legacy_cell_id")
            active_id = cell.get("cell_id")
            subject_key = cell.get("subject_key")
            if (not isinstance(published_id, str) or not published_id
                    or published_id in form_cell_ids):
                errors.append(f"form has a missing or duplicate cell ID: {slug}")
            else:
                form_cell_ids.add(published_id)
                qualified = f"{slug}:{published_id}"
                if qualified in corpus_cell_ids:
                    errors.append(f"corpus has a duplicate cell identity: {qualified}")
                corpus_cell_ids.add(qualified)
            if (not isinstance(legacy_id, str) or not legacy_id
                    or legacy_id in form_legacy_ids):
                errors.append(f"form has a missing or duplicate legacy cell ID: {slug}")
            else:
                form_legacy_ids.add(legacy_id)
            if (not isinstance(subject_key, str) or not subject_key
                    or subject_key in form_subject_keys):
                errors.append(f"form has a missing or duplicate subject key: {slug}")
            else:
                form_subject_keys.add(subject_key)
                qualified = f"{slug}:{subject_key}"
                if qualified in corpus_subject_keys:
                    errors.append(
                        f"corpus has a duplicate subject identity: {qualified}")
                corpus_subject_keys.add(qualified)

            ledger_state = cell.get("ledger_state")
            blocks_gate = cell.get("ledger_blocks_gate")
            if ledger_state not in LEDGER_STATES:
                errors.append(f"cell ledger state is invalid: {slug}/{published_id}")
            else:
                state_counts[ledger_state] += 1
                # A reviewed composite keeps the SUPPRESSED shape it had as a
                # retained subject -- no active cell id, reported under its
                # legacy id -- because the review changed its state, not its
                # emission.  What the review does change is that it stops
                # blocking, on a certificate this gate re-derives above.
                suppressed_shape = ledger_state in LEDGER_SUPPRESSED_STATES
                expected_block = (
                    ledger_state not in LEDGER_NONBLOCKING_STATES)
                if blocks_gate is not expected_block:
                    errors.append(
                        f"cell ledger blocking relation is false: {slug}/{published_id}")
                if blocks_gate is True:
                    blocking_cells += 1
                    if cell.get("comparison_status") == "excepted":
                        excused_blocking_cells += 1
                if suppressed_shape and active_id is not None:
                    errors.append(
                        f"retained cell publishes an active ID: {slug}/{published_id}")
                if (not suppressed_shape
                        and (not isinstance(active_id, str)
                             or active_id != published_id)):
                    errors.append(
                        f"active cell identity is invalid: {slug}/{published_id}")
                if suppressed_shape and published_id != legacy_id:
                    errors.append(
                        f"retained cell identity is invalid: {slug}/{published_id}")
                if not _string_list(
                        cell.get("ledger_reason_codes"),
                        nonempty=ledger_state != "active_resolved"):
                    errors.append(
                        f"cell ledger reasons are invalid: {slug}/{published_id}")
            if not isinstance(blocks_gate, bool):
                errors.append(f"cell ledger blocking flag is not boolean: {slug}")
            if not _is_sha256(cell.get("ledger_topology_sha256")):
                errors.append(f"cell topology digest is invalid: {slug}")
            page = cell.get("page")
            bbox = cell.get("bbox")
            if not _is_count(page) or page < 1:
                errors.append(f"cell page is invalid: {slug}/{published_id}")
            if (not _finite_number_list(bbox, length=4)
                    or not (bbox[0] < bbox[2] and bbox[1] < bbox[3])):
                errors.append(f"cell bbox is invalid: {slug}/{published_id}")
            latticed = cell.get("latticed")
            if not _is_count(latticed) or latticed < 1:
                errors.append(f"cell lattice count is invalid: {slug}/{published_id}")
            divider_x = cell.get("lattice_divider_x")
            expected_dividers = max(0, latticed - 1) if _is_count(latticed) else 0
            if (not _finite_number_list(divider_x, length=expected_dividers)
                    or any(left >= right for left, right in zip(
                        divider_x or [], (divider_x or [])[1:]))
                    or (_finite_number_list(bbox, length=4)
                        and any(not (bbox[0] < value < bbox[2])
                                for value in (divider_x or [])))):
                errors.append(
                    f"cell lattice divider geometry is invalid: {slug}/{published_id}")
            emitted = cell.get("emitted")
            if emitted is not None and not _is_count(emitted):
                errors.append(f"cell emitted count is invalid: {slug}/{published_id}")
            indexes_valid = cell.get("emitted_indexes_valid")
            if not isinstance(indexes_valid, bool):
                errors.append(
                    f"cell emitted-index flag is invalid: {slug}/{published_id}")
            if cell.get("ledger_state") in LEDGER_SUPPRESSED_STATES:
                # A suppressed subject emits NOTHING by design -- that is
                # what suppression means, and the emission inventory already
                # accounts for it. Counting emitted=None against its legacy
                # comb count filed all 29 composites plus the one retained
                # subject as emission mismatches the moment they existed.
                pass
            elif emitted != cell.get("latticed") or indexes_valid is not True:
                emission_mismatches += 1

            referee = cell.get("referee")
            try:
                expected_comparison = _comparison_for_cell(
                    cell, audit_evidence["complete"])
            except (TypeError, ValueError) as error:
                errors.append(
                    f"cell comparison is not derivable: {slug}/{published_id}: "
                    f"{error}")
                expected_comparison = (
                    "unevaluable", "comparison evidence is malformed")
            # Mirror of comb_referee.reviewed_exception_status. A reviewed
            # exception excuses ONE named unevaluable verdict on ONE subject,
            # so the gate re-derives that verdict itself and requires the
            # registry entry to name exactly it. An exception can never
            # launder a `stop`, and one whose recorded refusal no longer
            # matches the live one is STALE -- an error, not a pass.
            if cell.get("comparison_status") == "excepted":
                entry = _load_review_registry(
                    ).REVIEWED_UNEVALUABLE_EXCEPTIONS.get(
                    (str(slug), int(cell.get("page") or 0),
                     str(cell.get("cell_id") or cell.get("legacy_cell_id"))))
                underlying = expected_comparison
                if entry is None:
                    errors.append(
                        f"cell claims an exception no registry names: "
                        f"{slug}/{published_id}")
                elif underlying[0] != "unevaluable":
                    errors.append(
                        f"an exception excuses a non-unevaluable verdict: "
                        f"{slug}/{published_id}")
                elif entry.get("reason") != underlying[1]:
                    errors.append(
                        f"reviewed exception is stale: {slug}/{published_id}")
                elif entry.get("subject_key") != cell.get("subject_key"):
                    errors.append(
                        f"reviewed exception binds another subject: "
                        f"{slug}/{published_id}")
                else:
                    expected_comparison = (
                        "excepted", f"reviewed exception: {underlying[1]}")
            actual_comparison = (
                cell.get("comparison_status"), cell.get("comparison_reason"))
            if actual_comparison != expected_comparison:
                errors.append(
                    f"cell comparison relation is false: {slug}/{published_id}")
            comparison_status = expected_comparison[0]
            cell_comparisons[comparison_status] += 1
            if ledger_state in LEDGER_STATES:
                expected_transition = _transition_for_cell(
                    ledger_state, comparison_status)
                actual_transition = (
                    cell.get("transition_status"), cell.get("transition_reason"))
                if actual_transition != expected_transition:
                    errors.append(
                        f"cell transition relation is false: {slug}/{published_id}")
                if expected_transition[0] != "none":
                    pending += 1
            else:
                errors.append(f"cell transition is not evaluable: {slug}")

            if not isinstance(referee, dict) or referee.get("status") not in {
                    "measured", "unevaluable", "composite"}:
                errors.append(f"cell source result is malformed: {slug}")
            elif referee["status"] == "composite":
                # A composite is MEASURED -- on its corroboration, not on a
                # band -- so it must never be filed as source-unevaluable.
                # Its schema is re-derived here rather than trusted.
                composite_cells += 1
                if set(referee) != COMPOSITE_REFEREE_KEYS:
                    errors.append(
                        f"composite source certificate schema is "
                        f"unsupported: {slug}/{published_id}")
                elif (referee["criterion"]
                        not in COMPOSITE_SUPPRESSION_CRITERIA):
                    errors.append(
                        f"composite certificate names an untabled criterion: "
                        f"{slug}/{published_id}")
                elif not isinstance(referee["corroborated"], bool):
                    errors.append(
                        f"composite certificate publishes no boolean verdict: "
                        f"{slug}/{published_id}")
                elif cell.get("ledger_state") != "active_composite":
                    errors.append(
                        f"composite certificate on a non-composite subject: "
                        f"{slug}/{published_id}")
            elif referee["status"] == "measured":
                measured_cells += 1
                source_page = next((
                    item for item in pages
                    if isinstance(item, dict)
                    and item.get("page") == cell.get("page")
                ), None)
                if (not isinstance(source_page, dict)
                        or not _is_count(source_page.get("vector_paints"))
                        or source_page["vector_paints"] < 1):
                    errors.append(
                        f"measured source page has no vector paint: "
                        f"{slug}/{published_id}")
                certificate_errors = _measured_referee_certificate_errors(
                    str(slug), cell, referee)
                errors.extend(certificate_errors)
                if not certificate_errors:
                    if referee["compartments"] != cell.get("latticed"):
                        layout_mismatches += 1
                    if referee["positions_match"] is not True:
                        position_mismatches += 1
            else:
                source_unevaluable_cells += 1
                if (not isinstance(referee.get("reason"), str)
                        or not referee["reason"]
                        or any(key in referee for key in (
                            "error", "errors", "blockers"))):
                    errors.append(
                        f"unevaluable source result hides errors: {slug}")

            four_way = cell.get("four_way")
            expected_four_way = {
                "referee": (
                    referee.get("compartments")
                    if isinstance(referee, dict)
                    and referee.get("status") == "measured" else None),
                "lattice": cell.get("latticed"),
                "audit": cell.get("audit_printed"),
                "emitted": emitted,
            }
            if four_way != expected_four_way:
                errors.append(f"cell four-way publication is false: {slug}")

        inference_blockers = 0
        form_inference_ids: set[str] = set()
        form_inference_keys: set[str] = set()
        for inference in inferences:
            if not isinstance(inference, dict) or set(inference) != INFERENCE_KEYS:
                errors.append(f"form has malformed inference evidence: {slug}")
                continue
            if (not _is_count(inference.get("page"))
                    or inference.get("page", 0) < 1
                    or not _finite_number_list(inference.get("bbox"), length=4)
                    or not (inference["bbox"][0] < inference["bbox"][2]
                            and inference["bbox"][1] < inference["bbox"][3])):
                errors.append(f"inference geometry is invalid: {slug}")
            inference_id = inference.get("cell_id")
            subject_key = inference.get("subject_key")
            if (not isinstance(inference_id, str) or not inference_id
                    or inference_id in form_inference_ids
                    or inference_id in form_cell_ids):
                errors.append(f"form has a duplicate inference cell ID: {slug}")
            else:
                form_inference_ids.add(inference_id)
                qualified = f"{slug}:{inference_id}"
                if qualified in corpus_cell_ids:
                    errors.append(
                        f"corpus has a duplicate inferred cell identity: {qualified}")
                corpus_cell_ids.add(qualified)
            if (not isinstance(subject_key, str) or not subject_key
                    or subject_key in form_inference_keys
                    or subject_key in form_subject_keys):
                errors.append(f"form has a duplicate inference subject key: {slug}")
            else:
                form_inference_keys.add(subject_key)
                qualified = f"{slug}:{subject_key}"
                if qualified in corpus_subject_keys:
                    errors.append(
                        f"corpus has a duplicate inferred subject identity: {qualified}")
                corpus_subject_keys.add(qualified)
            if (inference.get("state") != INFERENCE_STATE
                    or inference.get("blocks_gate") is not True):
                errors.append(f"inference state/blocking relation is false: {slug}")
            else:
                inference_blockers += 1
            if (not _string_list(inference.get("reason_codes"), nonempty=True)
                    or not _is_sha256(inference.get("topology_sha256"))):
                errors.append(f"inference provenance is malformed: {slug}")

        active_resolved = state_counts["active_resolved"]
        active_unresolved = state_counts["active_unresolved"]
        retained = state_counts["retained_unresolved"]
        composite = state_counts["active_composite"]
        derived_counts = {
            "combs": len(cells),
            "subjects": len(cells),
            "subjects_active": (
                active_resolved + active_unresolved + composite),
            "subjects_active_resolved": active_resolved,
            "subjects_active_unresolved": active_unresolved,
            "subjects_retained_unresolved": retained,
            "inferences_suppressed": len(inferences),
            "ledger_blocking": blocking_cells + inference_blockers,
            # re-derived from the cells, never read off the report: a
            # blocker is excused exactly when its own cell's comparison is
            # an excepted one (each already registry-re-derived above).
            "ledger_blocking_excused": excused_blocking_cells,
            "measured": measured_cells,
            "composite": composite_cells,
            "source_unevaluable": source_unevaluable_cells,
            "unevaluable": cell_comparisons["unevaluable"],
            "referee_layout_mismatches": layout_mismatches,
            "referee_layout_position_mismatches": position_mismatches,
            "emission_layout_mismatches": emission_mismatches,
        }
        for key, actual in derived_counts.items():
            if counts.get(key) != actual:
                errors.append(f"form total disagrees with evidence: {slug}/{key}")
        # Both partitions must still account for EVERY cell exactly once, so
        # a composite has to be counted on both axes -- by ledger state and by
        # measurement kind.  Leaving it out of either would let a whole class
        # of subject pass through unreconciled, which is the fault this
        # identity exists to catch.
        if (len(cells) != (active_resolved + active_unresolved + retained
                           + composite)
                or (measured_cells + source_unevaluable_cells
                    + composite_cells) != len(cells)
                or sum(cell_comparisons.values()) != len(cells)):
            errors.append(f"form subject partitions are inconsistent: {slug}")
        stats["pending_transitions"] += pending
        if cell_comparisons != form_comparisons:
            errors.append(f"cell comparison totals disagree: {slug}")
        derived_status, derived_reason = _form_status_relation(
            ledger_blocking=derived_counts["ledger_blocking"],
            ledger_blocking_excused=derived_counts[
                "ledger_blocking_excused"],
            emission_inventory=emission_inventory,
            audit_evidence=audit_evidence,
            comparisons=cell_comparisons,
        )
        if form_status != derived_status or form.get("reason") != derived_reason:
            errors.append(f"form status/reason relation is false: {slug}")
        recomputed[f"forms_{derived_status}"] += 1
        for key in (
                "combs", "measured", "composite", "source_unevaluable",
                "unevaluable",
                "ledger_blocking", "ledger_blocking_excused",
                "subjects_active", "subjects_active_resolved",
                "subjects_active_unresolved", "subjects_retained_unresolved",
                "inferences_suppressed", "referee_layout_mismatches",
                "referee_layout_position_mismatches",
                "emission_layout_mismatches"):
            recomputed[key] += derived_counts[key]
        for name in COMPARISON_NAMES:
            recomputed[f"comparison:{name}"] += cell_comparisons[name]

    total_pairs = {
        "forms_measured": len(forms),
        "forms_error": len(raw_errors),
        "combs_found": recomputed["combs"],
        "combs_measured": recomputed["measured"],
        "combs_composite": recomputed["composite"],
        "combs_source_unevaluable": recomputed["source_unevaluable"],
        "combs_unevaluable": recomputed["unevaluable"],
        "ledger_blocking": recomputed["ledger_blocking"],
        "ledger_blocking_excused": recomputed["ledger_blocking_excused"],
        "subjects_active": recomputed["subjects_active"],
        "subjects_active_resolved": recomputed["subjects_active_resolved"],
        "subjects_active_unresolved": recomputed["subjects_active_unresolved"],
        "subjects_retained_unresolved": (
            recomputed["subjects_retained_unresolved"]),
        "inferences_suppressed": recomputed["inferences_suppressed"],
        "referee_layout_mismatches": (
            recomputed["referee_layout_mismatches"]),
        "referee_layout_position_mismatches": (
            recomputed["referee_layout_position_mismatches"]),
        "forms_ok": recomputed["forms_ok"],
        "forms_disagreement": recomputed["forms_disagreement"],
        "forms_unevaluable": recomputed["forms_unevaluable"],
        "audit_evidence_complete_forms": (
            recomputed["audit_evidence_complete_forms"]),
    }
    for key, actual in total_pairs.items():
        if totals.get(key) != actual:
            errors.append(f"report total disagrees with forms: {key}")
    if comparisons != {
            name: recomputed[f"comparison:{name}"]
            for name in COMPARISON_NAMES}:
        errors.append("report comparison totals disagree with forms")
    if (totals.get("forms_expected") != expected_forms
            or totals.get("combs_expected") != expected_subjects
            or totals.get("combs_found") != expected_subjects):
        errors.append("report corpus identity is incomplete")
    if (sum(comparisons.values()) != totals.get("combs_found")
            or totals.get("combs_measured", 0)
            + totals.get("combs_composite", 0)
            + totals.get("combs_source_unevaluable", 0)
            != totals.get("combs_found")):
        errors.append("report subject partition is inconsistent")
    if totals.get("referee_attestation_complete") is not attestation.get(
            "complete"):
        errors.append("report attestation-complete total is false")
    if totals.get("referee_enforceable") is not attestation.get("enforceable"):
        errors.append("report enforceable total is false")

    coverage_ok = bool(
        len(forms) == expected_forms
        and len(slugs) == expected_forms
        and not raw_errors
        and recomputed["combs"] == expected_subjects
    )
    derived_status_reasons: list[str] = []
    if not coverage_ok or recomputed["forms_unevaluable"]:
        application_status = "unevaluable"
        derived_status_reasons.append(
            "corpus coverage or one or more forms are unevaluable")
    elif recomputed["forms_disagreement"]:
        application_status = "disagreement"
        derived_status_reasons.append(
            "one or more four-way form comparisons disagree")
    else:
        application_status = "ok"
    derived_report_status = application_status
    if attestation.get("complete") is not True:
        derived_report_status = "unevaluable"
        derived_status_reasons.append(
            "standalone referee runtime/application attestation is incomplete "
            "and non-enforceable")
    if status != derived_report_status or reasons != derived_status_reasons:
        errors.append("report status/reasons relation is false")
    stats.update({
        "referee_layout_mismatches": (
            recomputed["referee_layout_mismatches"]),
        "referee_layout_position_mismatches": (
            recomputed["referee_layout_position_mismatches"]),
        "emission_layout_mismatches": (
            recomputed["emission_layout_mismatches"]),
        "application_status": application_status,
    })
    return errors, stats


FORM_ARTIFACT_KEYS = {
    "ir_sha256", "layout_sha256", "html_sha256",
    "html_structure_sha256", "guide_sha256", "guide_html_sha256",
    "tracked_provenance_file", "tracked_provenance_sha256",
}
FORM_SOURCE_KEYS = {"file", "sha256", "bytes", "page_count", "layout_pin"}
HTML_GEOMETRY_EPSILON_PT = 0.0002
REFEREE_POSITION_TOLERANCE_PT = 0.25
REFEREE_ROUNDING_EPSILON_PT = 0.00001
REFEREE_PARTIAL_ANCHOR_REASON = (
    "ledger-owned active subject has full-band Poppler proof of erased "
    "lattice anchors"
)
REFEREE_MEASURED_REASONS = {
    "one source topology contains every recognised anchor",
    (
        "one richer source topology contains every other slab and "
        "occupies a strict majority of the comb band"
    ),
    REFEREE_PARTIAL_ANCHOR_REASON,
}


def _same_finite_numbers(left: Any, right: Any) -> bool:
    return (
        _finite_number_list(left)
        and _finite_number_list(right)
        and len(left) == len(right)
        and all(abs(float(a) - float(b)) <= 1e-9
                for a, b in zip(left, right))
    )


def _rounded_six(value: Any) -> float:
    return round(float(value), 6)


def _referee_topology_contains(
        superset: tuple[float, ...], subset: tuple[float, ...],
        ) -> bool:
    available = list(superset)
    for value in subset:
        choices = sorted(
            (abs(candidate - value), index)
            for index, candidate in enumerate(available)
            if abs(candidate - value) <= REFEREE_POSITION_TOLERANCE_PT
        )
        if not choices:
            return False
        _distance, index = choices[0]
        available.pop(index)
    return True


def _referee_topology_key(values: Sequence[float]) -> str:
    return ",".join(str(_rounded_six(value)) for value in values)


_REVIEW_REGISTRY = None


def _load_review_registry():
    """Load the reviewed-ledger registries by explicit pinned path.

    Same isolation-proof pattern the referee uses: this gate runs children
    with `-I`, and a bare import would resolve differently (or not at all)
    depending on how it was invoked.
    """
    global _REVIEW_REGISTRY
    if _REVIEW_REGISTRY is None:
        import importlib.util
        path = HERE / "review_registry.py"
        spec = importlib.util.spec_from_file_location("review_registry", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _REVIEW_REGISTRY = module
    # ONE module instance for the process. Re-executing the file per call
    # would hand every caller its own copy of the registries, so a test that
    # emptied them would be emptying a copy nobody else reads -- which is
    # exactly what happened when this was written without the cache.
    return _REVIEW_REGISTRY


def _reviewed_registry_coverage_errors(cells: "Iterable[dict[str, Any]]"
                                       ) -> list[str]:
    """Every reviewed entry applied EXACTLY ONCE across the whole corpus.

    This is the corpus half of a guard that cannot be completed per document.
    A form CODE plus revision is not a document identity -- 1701's main
    sheet, its attachment and its consolidation all publish code 1701
    revision 2018 -- so `lattice.apply_reviewed_*` passes over entries whose
    pinned source bytes are not its own, unable to tell a sibling document
    from a re-pinned PDF from where it stands.  Here every document is
    visible at once, so the question becomes answerable and is answered:

      * an entry that landed NOWHERE is a review of bytes the corpus no
        longer contains, or of a subject that no longer exists;
      * an entry that landed TWICE means two documents accepted the same
        decision, which the sha scoping is supposed to make impossible;
      * a published certificate naming no entry is a forged promotion.

    All three are errors.  Reported from the report's own published
    certificates, never from the layouts, so a producer that lied about what
    it applied is caught rather than believed.
    """
    registry = _load_review_registry()
    wanted: set[tuple[str, ...]] = set()
    for key in registry.REVIEWED_LEDGER_RESOLUTIONS:
        wanted.add(("resolution", str(key[0]), str(key[1]), str(key[2])))
    for key in registry.REVIEWED_LEDGER_TRANSITIONS:
        wanted.add(("transition", str(key[0]), str(key[1]), str(key[2])))
    for key in registry.REVIEWED_UNEVALUABLE_EXCEPTIONS:
        wanted.add(("exception", str(key[0]), str(key[1]), str(key[2])))
    seen: dict[tuple[str, ...], int] = {}
    for cell in cells:
        for kind, field in (("resolution", "resolution_certificate"),
                            ("transition", "transition_certificate")):
            certificate = cell.get(field)
            if not isinstance(certificate, dict):
                continue
            raw = certificate.get("registry_key")
            if (not isinstance(raw, list) or len(raw) != 3):
                return [f"certificate publishes no registry key: "
                        f"{cell.get('cell')}"]
            item = (kind, str(raw[0]), str(raw[1]), str(raw[2]))
            seen[item] = seen.get(item, 0) + 1
        raw_exception = cell.get("exception_registry_key")
        if raw_exception is not None:
            if (not isinstance(raw_exception, list)
                    or len(raw_exception) != 3
                    or cell.get("comparison_status") != "excepted"):
                return [f"exception key without an excepted comparison: "
                        f"{cell.get('cell')}"]
            item = ("exception", str(raw_exception[0]),
                    str(raw_exception[1]), str(raw_exception[2]))
            seen[item] = seen.get(item, 0) + 1
        elif cell.get("comparison_status") == "excepted":
            return [f"excepted cell publishes no exception key: "
                    f"{cell.get('cell')}"]
    errors: list[str] = []
    for item in sorted(wanted - set(seen)):
        errors.append("reviewed entry was applied nowhere in the corpus: "
                      f"{item[0]} {item[1]}/p{item[2]}/{item[3]}")
    for item in sorted(set(seen) - wanted):
        errors.append("published certificate names no reviewed entry: "
                      f"{item[0]} {item[1]}/p{item[2]}/{item[3]}")
    for item, count in sorted(seen.items()):
        if item in wanted and count != 1:
            errors.append(f"reviewed entry applied {count} times: "
                          f"{item[0]} {item[1]}/p{item[2]}/{item[3]}")
    return errors


def _rail_derivation_errors(
        label: str, referee: dict[str, Any], cell: dict[str, Any],
        ) -> list[str]:
    """Cross-check each rail's published basis against the topology.

    The referee names why each rail sits where it does (R1, F232).  The gate
    cannot re-count Poppler glyphs, but every basis makes claims the published
    numbers must satisfy, and each is re-derived here:

      * "owner-edge" -- the rail IS the rectangle's edge on that side;
      * "wall-outside-run" -- the rail is a measured boundary strictly inside
        the rectangle, and wall_x is that rail;
      * "prose-refuted-outer-region" -- the rail is a measured boundary,
        from_x is the rectangle edge it refused, span_pt is exactly the paper
        between them, and more than ONE glyph stood in it (one glyph is the
        pre-printed decoration a compartment may carry; a refutation claiming
        less refutes nothing).

    "prose-and-structure-conflict" may never reach a measured band -- the
    referee publishes that shape only on its own unevaluable verdict.
    """
    errors: list[str] = []
    derivation = referee.get("rail_derivation")
    rails = referee.get("source_rail_x")
    source = referee.get("source_divider_x")
    bbox = cell.get("bbox")
    if (not isinstance(derivation, dict)
            or set(derivation) != {"left", "right"}
            or not _finite_number_list(rails, length=2)
            or not _finite_number_list(source)
            or not _finite_number_list(bbox, length=4)):
        return [f"rail derivation is malformed: {label}"]
    source_values = {_rounded_six(value) for value in source}
    for side, rail, edge in (
            ("left", _rounded_six(rails[0]), _rounded_six(bbox[0])),
            ("right", _rounded_six(rails[1]), _rounded_six(bbox[2]))):
        evidence = derivation.get(side)
        if not isinstance(evidence, dict):
            errors.append(f"rail derivation side is malformed: {label}/{side}")
            continue
        basis = evidence.get("basis")
        if basis not in RAIL_DERIVATION_BASES:
            errors.append(
                f"rail derivation basis is unsupported: {label}/{side}")
            continue
        if basis == "owner-edge":
            if set(evidence) != {"basis"} or rail != edge:
                errors.append(
                    f"owner-edge rail is not the owner's edge: {label}/{side}")
        elif basis == "wall-outside-run":
            if (set(evidence) != {"basis", "wall_x"}
                    or not _finite_number(evidence.get("wall_x"))
                    or _rounded_six(evidence["wall_x"]) != rail
                    or rail not in source_values):
                errors.append(
                    f"wall rail is not a measured boundary: {label}/{side}")
        else:
            span = evidence.get("span_pt")
            glyphs = evidence.get("glyphs")
            if (set(evidence) != {"basis", "from_x", "span_pt", "glyphs"}
                    or not _finite_number(evidence.get("from_x"))
                    or not _finite_number(span)
                    or _rounded_six(evidence["from_x"]) != edge
                    or rail not in source_values
                    or _rounded_six(span) != _rounded_six(abs(rail - edge))
                    or float(span) <= 0
                    or not _is_count(glyphs)
                    or glyphs <= 1):
                errors.append(
                    f"prose refutation evidence is false: {label}/{side}")
    if not errors:
        # A rail strictly inside the rectangle must say which measurement put
        # it there; "owner-edge" on an interior rail is a rail that moved
        # without naming why.
        for side, rail, edge in (
                ("left", _rounded_six(rails[0]), _rounded_six(bbox[0])),
                ("right", _rounded_six(rails[1]), _rounded_six(bbox[2]))):
            basis = derivation[side].get("basis")
            if rail != edge and basis == "owner-edge":
                errors.append(
                    f"interior rail carries no derivation: {label}/{side}")
    return errors


def _measured_referee_certificate_errors(
        slug: str, cell: dict[str, Any], referee: dict[str, Any],
        ) -> list[str]:
    """Independently derive the producer's measured-source acceptance proof."""
    cell_id = cell.get("cell")
    label = f"{slug}/{cell_id}"
    errors: list[str] = []
    if set(referee) == PARTIAL_ANCHOR_REFEREE_KEYS:
        return _partial_anchor_referee_certificate_errors(slug, cell, referee)
    if set(referee) != MEASURED_REFEREE_KEYS:
        return [f"measured source certificate schema is unsupported: {label}"]
    errors.extend(_rail_derivation_errors(label, referee, cell))
    reason = referee.get("reason")
    if reason not in REFEREE_MEASURED_REASONS:
        errors.append(f"measured source reason is not derived: {label}")

    lattice = cell.get("lattice_divider_x")
    source = referee.get("source_divider_x")
    rails = referee.get("source_rail_x")
    extras = referee.get("extra_divider_x")
    chosen = referee.get("chosen_topology")
    compartments = referee.get("compartments")
    if (not _finite_number_list(lattice)
            or not _finite_number_list(source)
            or not _finite_number_list(rails, length=2)
            or not _finite_number_list(extras)
            or not _finite_number_list(chosen)
            or not _is_count(compartments)
            or compartments < 2):
        return [*errors, f"measured source topology is malformed: {label}"]
    lattice_values = [_rounded_six(value) for value in lattice]
    source_values = [_rounded_six(value) for value in source]
    rail_values = [_rounded_six(value) for value in rails]
    extra_values = [_rounded_six(value) for value in extras]
    chosen_values = [_rounded_six(value) for value in chosen]
    if (any(float(value) != rounded for value, rounded in zip(
                source, source_values))
            or any(float(value) != rounded for value, rounded in zip(
                rails, rail_values))
            or any(float(value) != rounded for value, rounded in zip(
                extras, extra_values))
            or any(float(value) != rounded for value, rounded in zip(
                chosen, chosen_values))):
        errors.append(f"measured source coordinates exceed fixed precision: {label}")
    # A comb is bounded by the RAILS the referee measured, not by the subject
    # rectangle: one rectangle can rule a caption or a dash box beside the
    # comb. So the compartment count is the count between those rails, and the
    # rails must be a pair the published dividers actually sit inside.
    enclosed_values = [
        value for value in source_values
        if rail_values[0] < value < rail_values[1]
    ]
    if (source_values != sorted(set(source_values))
            or extra_values != sorted(set(extra_values))
            or chosen_values != source_values
            or rail_values[0] >= rail_values[1]
            or compartments != len(enclosed_values) + 1):
        errors.append(f"measured source topology relation is false: {label}")
    bbox = cell.get("bbox")
    if _finite_number_list(bbox, length=4):
        if any(not (float(bbox[0]) < value < float(bbox[2]))
               for value in source_values):
            errors.append(
                f"measured source divider lies outside its owner: {label}")
        # A rail is either a boundary the source drew inside the rectangle or
        # the rectangle's own edge; it is never outside, and never a value the
        # referee did not measure.
        if any(
            not (float(bbox[0]) <= value <= float(bbox[2]))
            or (float(bbox[0]) < value < float(bbox[2])
                and value not in source_values)
            for value in rail_values
        ):
            errors.append(f"measured source rail is not the owner's: {label}")

    anchor_matches = referee.get("anchor_matches")
    anchor_sources: list[float] = []
    anchor_pairs: list[tuple[float, float]] = []
    anchor_relation_valid = True
    if (not isinstance(anchor_matches, list)
            or len(anchor_matches) != len(lattice_values)):
        errors.append(f"measured anchor inventory is incomplete: {label}")
        anchor_relation_valid = False
    else:
        for expected_layout, match in zip(lattice_values, anchor_matches):
            if (not isinstance(match, dict)
                    or set(match) != {"layout_x", "source_x", "delta_pt"}
                    or not all(_finite_number(match.get(key)) for key in (
                        "layout_x", "source_x", "delta_pt"))):
                errors.append(f"measured anchor evidence is malformed: {label}")
                anchor_relation_valid = False
                continue
            layout_x = _rounded_six(match["layout_x"])
            source_x = _rounded_six(match["source_x"])
            delta = _rounded_six(match["delta_pt"])
            expected_delta = _rounded_six(source_x - layout_x)
            if (float(match["layout_x"]) != layout_x
                    or float(match["source_x"]) != source_x
                    or float(match["delta_pt"]) != delta
                    or layout_x != expected_layout
                    or delta != expected_delta):
                errors.append(f"measured anchor relation is false: {label}")
                anchor_relation_valid = False
            anchor_sources.append(source_x)
            anchor_pairs.append((layout_x, source_x))
    derived_positions_match = bool(
        anchor_relation_valid
        and len(anchor_sources) == len(lattice_values)
        and all(abs(source_x - layout_x)
                <= REFEREE_POSITION_TOLERANCE_PT
                for layout_x, source_x in anchor_pairs)
    )
    if (referee.get("anchors_complete") is not True
            or referee.get("positions_match") is not derived_positions_match):
        errors.append(f"measured anchor verdict is false: {label}")
    if any(
            abs(extra - anchor) <= REFEREE_POSITION_TOLERANCE_PT
            for extra in extra_values for anchor in anchor_sources):
        errors.append(f"measured extra divider duplicates an anchor: {label}")
    derived_source = sorted(set(anchor_sources) | set(extra_values))
    if source_values != derived_source:
        errors.append(f"measured source divider inventory is false: {label}")

    components = referee.get("components")
    component_x: list[float] = []
    if not isinstance(components, list) or not components:
        errors.append(f"measured source components are missing: {label}")
    else:
        for component in components:
            if (not isinstance(component, dict)
                    or set(component) != {
                        "x", "x0", "x1", "tone", "elements", "clipped"}
                    or not all(_finite_number(component.get(key)) for key in (
                        "x", "x0", "x1", "tone"))
                    or not isinstance(component.get("elements"), list)
                    or not component["elements"]
                    or not all(isinstance(item, str) and item
                               for item in component["elements"])
                    or len(component["elements"])
                    != len(set(component["elements"]))
                    or component.get("clipped") is not False):
                errors.append(f"measured source component is malformed: {label}")
                continue
            x = _rounded_six(component["x"])
            x0 = _rounded_six(component["x0"])
            x1 = _rounded_six(component["x1"])
            tone = float(component["tone"])
            if (x0 >= x1 or x != _rounded_six((x0 + x1) / 2)
                    or not 0.0 <= tone <= 1.0):
                errors.append(f"measured source component relation is false: {label}")
            component_x.append(x)
        if component_x != sorted(component_x):
            errors.append(f"measured source components are not ordered: {label}")
        if (any(not any(abs(component - divider)
                        <= REFEREE_POSITION_TOLERANCE_PT
                        for divider in source_values)
                for component in component_x)
                or any(not any(abs(component - divider)
                               <= REFEREE_POSITION_TOLERANCE_PT
                               for component in component_x)
                       for divider in source_values)):
            errors.append(f"measured components do not bind the topology: {label}")

    proofs = referee.get("subject_gap_proofs")
    unproven = referee.get("unproven_subject_gaps")
    adjacent_anchors = set(zip(anchor_sources, anchor_sources[1:]))
    seen_proofs: set[tuple[float, float]] = set()
    if not isinstance(proofs, list):
        errors.append(f"measured subject-gap proofs are malformed: {label}")
    else:
        for proof in proofs:
            keys = {
                "left", "right", "gap_pt", "pitch_pt",
                "integral_residual_pt", "single_frame_elements",
                "unsupported_regions",
            }
            if (not isinstance(proof, dict) or set(proof) != keys
                    or not all(_finite_number(proof.get(key)) for key in (
                        "left", "right", "gap_pt", "pitch_pt",
                        "integral_residual_pt"))
                    or not isinstance(proof.get("single_frame_elements"), list)
                    or not proof["single_frame_elements"]
                    or not all(isinstance(item, str) and item
                               for item in proof["single_frame_elements"])
                    or len(proof["single_frame_elements"])
                    != len(set(proof["single_frame_elements"]))
                    or proof.get("unsupported_regions") != []):
                errors.append(f"measured subject-gap proof is malformed: {label}")
                continue
            left = _rounded_six(proof["left"])
            right = _rounded_six(proof["right"])
            pair = (left, right)
            if (pair not in adjacent_anchors or pair in seen_proofs
                    or _rounded_six(proof["gap_pt"])
                    != _rounded_six(right - left)
                    or float(proof["pitch_pt"]) <= 0
                    or float(proof["integral_residual_pt"]) < 0):
                errors.append(f"measured subject-gap proof is false: {label}")
            seen_proofs.add(pair)
    if unproven != []:
        errors.append(f"measured source has unproven subject gaps: {label}")

    vertical_names = (
        "y0", "y1", "contract_y0", "contract_y1", "open_y0", "open_y1",
        "contract_span_pt", "seed_span_pt", "measured_span_pt",
        "unmeasured_span_pt",
    )
    if not all(_finite_number(referee.get(name)) for name in vertical_names):
        return [*errors, f"measured source span evidence is malformed: {label}"]
    y0, y1, contract_y0, contract_y1, open_y0, open_y1 = (
        float(referee[name]) for name in vertical_names[:6])
    contract_span = float(referee["contract_span_pt"])
    seed_span = float(referee["seed_span_pt"])
    measured_span = float(referee["measured_span_pt"])
    unmeasured_span = float(referee["unmeasured_span_pt"])
    if (not contract_y0 <= open_y0 < open_y1 <= contract_y1
            or not open_y0 <= y0 < y1 <= open_y1
            or y1 - y0 <= REFEREE_POSITION_TOLERANCE_PT
            or _rounded_six(contract_span)
            != _rounded_six(contract_y1 - contract_y0)
            or _rounded_six(seed_span) != _rounded_six(open_y1 - open_y0)
            or measured_span <= seed_span / 2
            or measured_span > seed_span + REFEREE_ROUNDING_EPSILON_PT
            or _rounded_six(unmeasured_span)
            != _rounded_six(max(0.0, seed_span - measured_span))):
        errors.append(f"measured source span relation is false: {label}")

    coverage = referee.get("topology_coverage_pt")
    topology_by_key: dict[str, tuple[float, ...]] = {}
    if not isinstance(coverage, dict) or not coverage:
        errors.append(f"measured topology coverage is missing: {label}")
        coverage = {}
    else:
        for key, amount in coverage.items():
            try:
                values = tuple(float(item) for item in key.split(","))
            except (AttributeError, TypeError, ValueError):
                values = ()
            if (not isinstance(key, str) or not values
                    or not all(math.isfinite(value) for value in values)
                    or list(values) != sorted(set(values))
                    or key != _referee_topology_key(values)
                    or not _finite_number(amount) or float(amount) <= 0):
                errors.append(f"measured topology coverage is malformed: {label}")
                continue
            topology_by_key[key] = values
    chosen_key = _referee_topology_key(source_values)
    if chosen_key not in topology_by_key:
        errors.append(f"chosen source topology has no coverage: {label}")
    coverage_total = sum(
        float(amount) for amount in coverage.values()
        if _finite_number(amount))
    if abs(coverage_total - measured_span) > (
            REFEREE_ROUNDING_EPSILON_PT * max(1, len(coverage))):
        errors.append(f"measured topology coverage total is false: {label}")
    chosen_coverage = coverage.get(chosen_key)
    if (_finite_number(chosen_coverage)
            and float(chosen_coverage) + REFEREE_ROUNDING_EPSILON_PT
            < y1 - y0):
        errors.append(f"chosen band exceeds its topology coverage: {label}")

    topologies = sorted(topology_by_key.values())
    expected_relations = [
        {
            "candidate": list(candidate),
            "other": list(other),
            "contains": _referee_topology_contains(candidate, other),
            "proper": (
                len(candidate) > len(other)
                and _referee_topology_contains(candidate, other)
            ),
        }
        for candidate in topologies for other in topologies
        if candidate != other
    ]
    relations = referee.get("topology_superset_relations")
    if len(topologies) == 1:
        if (reason != "one source topology contains every recognised anchor"
                or relations != []
                or topologies[0] != tuple(source_values)
                or not _finite_number(chosen_coverage)
                or abs(float(chosen_coverage) - measured_span)
                > REFEREE_ROUNDING_EPSILON_PT):
            errors.append(f"single-topology acceptance relation is false: {label}")
    elif len(topologies) > 1:
        dominant = [
            candidate for candidate in topologies
            if all(
                other == candidate
                or (len(candidate) > len(other)
                    and _referee_topology_contains(candidate, other))
                for other in topologies
            )
            and _finite_number(coverage.get(_referee_topology_key(candidate)))
            and float(coverage[_referee_topology_key(candidate)]) > seed_span / 2
        ]
        if (reason != (
                    "one richer source topology contains every other slab and "
                    "occupies a strict majority of the comb band")
                or relations != expected_relations
                or dominant != [tuple(source_values)]):
            errors.append(f"multi-topology acceptance relation is false: {label}")

    ignored = referee.get("ignored_slabs")
    ignored_reasons = {
        "slab is no wider than the fixed position bound",
        "no candidate divider ink",
        "only cell-edge frames remain when an anchor is absent",
    }
    if not isinstance(ignored, list):
        errors.append(f"ignored source slabs are malformed: {label}")
    else:
        for slab in ignored:
            slab_keys = set(slab) if isinstance(slab, dict) else set()
            if (not isinstance(slab, dict)
                    or slab_keys not in (
                        {"y0", "y1", "reason"},
                        {"y0", "y1", "reason", "source_divider_x"})
                    or slab.get("reason") not in ignored_reasons
                    or not _finite_number(slab.get("y0"))
                    or not _finite_number(slab.get("y1"))
                    or float(slab["y0"]) >= float(slab["y1"])
                    or not (open_y0 <= float(slab["y0"])
                            < float(slab["y1"]) <= open_y1)):
                errors.append(f"ignored source slab is malformed: {label}")
                continue
            if "source_divider_x" in slab and not _finite_number_list(
                    slab["source_divider_x"]):
                errors.append(f"ignored source topology is malformed: {label}")
    return errors


def _partial_anchor_referee_certificate_errors(
        slug: str, cell: dict[str, Any], referee: dict[str, Any],
        ) -> list[str]:
    """Independently re-derive the partial-anchor acceptance proof.

    The referee's third measured-source shape claims a lattice anchor is
    ABSENT from the paper: the raw target-tone rail Poppler exposes at the
    anchor is exhaustively erased by one supported, unclipped, non-target
    final owner across the whole open band.  The gate must PROVE that claim
    from the published SVG-derived evidence rather than accept the
    certificate's own ``valid`` flag; every relation below is recomputed and
    any gap fails closed.  ``anchors_complete: false`` and
    ``positions_match: false`` are accepted for this kind ONLY — a declared
    anchor with no source position cannot match, and the referee holds both
    False deliberately.
    """
    cell_id = cell.get("cell")
    label = f"{slug}/{cell_id}"
    errors: list[str] = []
    epsilon = REFEREE_ROUNDING_EPSILON_PT
    if referee.get("reason") != REFEREE_PARTIAL_ANCHOR_REASON:
        errors.append(f"partial-anchor source reason is not derived: {label}")
    if cell.get("ledger_state") != "active_unresolved":
        errors.append(
            f"partial-anchor subject is not ledger-owned active: {label}")

    lattice = cell.get("lattice_divider_x")
    source = referee.get("source_divider_x")
    rails = referee.get("source_rail_x")
    extras = referee.get("extra_divider_x")
    chosen = referee.get("chosen_topology")
    missing = referee.get("missing_anchor_x")
    compartments = referee.get("compartments")
    if (not _finite_number_list(lattice)
            or not _finite_number_list(source)
            or not _finite_number_list(rails, length=2)
            or not _finite_number_list(extras)
            or not _finite_number_list(chosen)
            or not _finite_number_list(missing)
            or not _is_count(compartments)
            or compartments < 2):
        return [*errors,
                f"partial-anchor source topology is malformed: {label}"]
    errors.extend(_rail_derivation_errors(label, referee, cell))
    lattice_values = [_rounded_six(value) for value in lattice]
    source_values = [_rounded_six(value) for value in source]
    rail_values = [_rounded_six(value) for value in rails]
    chosen_values = [_rounded_six(value) for value in chosen]
    missing_values = [_rounded_six(value) for value in missing]
    if (any(float(value) != rounded for value, rounded in zip(
                source, source_values))
            or any(float(value) != rounded for value, rounded in zip(
                rails, rail_values))
            or any(float(value) != rounded for value, rounded in zip(
                chosen, chosen_values))
            or any(float(value) != rounded for value, rounded in zip(
                missing, missing_values))):
        errors.append(
            f"partial-anchor coordinates exceed fixed precision: {label}")
    # Same relation as the full-anchor path: the count is the compartments
    # between the measured rails, not across the subject rectangle.
    enclosed_values = [
        value for value in source_values
        if rail_values[0] < value < rail_values[1]
    ]
    if (source_values != sorted(set(source_values))
            or chosen_values != source_values
            or rail_values[0] >= rail_values[1]
            or compartments != len(enclosed_values) + 1):
        errors.append(
            f"partial-anchor source topology relation is false: {label}")
    if list(extras):
        # The partial path maps every interior source group one-to-one to a
        # declared anchor; an undeclared extra divider has no place here.
        errors.append(
            f"partial-anchor topology carries an extra divider: {label}")
    if not missing_values or missing_values != sorted(set(missing_values)):
        errors.append(
            f"partial-anchor missing-anchor inventory is invalid: {label}")
    bbox = cell.get("bbox")
    if _finite_number_list(bbox, length=4):
        if any(not (float(bbox[0]) < value < float(bbox[2]))
               for value in source_values):
            errors.append(
                f"partial-anchor source divider lies outside its owner: "
                f"{label}")
        if any(
            not (float(bbox[0]) <= value <= float(bbox[2]))
            or (float(bbox[0]) < value < float(bbox[2])
                and value not in source_values)
            for value in rail_values
        ):
            errors.append(
                f"partial-anchor source rail is not the owner's: {label}")

    anchor_matches = referee.get("anchor_matches")
    matched_layout: list[float] = []
    matched_sources: list[float] = []
    if (not isinstance(anchor_matches, list)
            or len(anchor_matches) + len(missing_values)
            != len(lattice_values)):
        return [*errors,
                f"partial-anchor anchor inventory is incomplete: {label}"]
    for match in anchor_matches:
        if (not isinstance(match, dict)
                or set(match) != {"layout_x", "source_x", "delta_pt"}
                or not all(_finite_number(match.get(key)) for key in (
                    "layout_x", "source_x", "delta_pt"))):
            return [*errors,
                    f"partial-anchor anchor evidence is malformed: {label}"]
        layout_x = _rounded_six(match["layout_x"])
        source_x = _rounded_six(match["source_x"])
        delta = _rounded_six(match["delta_pt"])
        if (float(match["layout_x"]) != layout_x
                or float(match["source_x"]) != source_x
                or float(match["delta_pt"]) != delta
                or delta != _rounded_six(source_x - layout_x)
                or abs(delta) > REFEREE_POSITION_TOLERANCE_PT):
            errors.append(
                f"partial-anchor anchor relation is false: {label}")
        matched_layout.append(layout_x)
        matched_sources.append(source_x)
    observed_layout = sorted(matched_layout)
    if (len(set(matched_layout)) != len(matched_layout)
            or sorted({*matched_layout, *missing_values})
            != lattice_values
            or set(matched_layout) & set(missing_values)):
        errors.append(
            f"partial-anchor inventory does not partition the lattice: "
            f"{label}")
    if (len(set(matched_sources)) != len(matched_sources)
            or sorted(matched_sources) != source_values):
        errors.append(
            f"partial-anchor source divider inventory is false: {label}")
    if referee.get("anchors_complete") is not False:
        errors.append(
            f"partial-anchor completeness flag is not honest: {label}")
    if referee.get("positions_match") is not False:
        errors.append(
            f"partial-anchor position verdict is not honest: {label}")

    components = referee.get("components")
    component_x: list[float] = []
    component_tones: list[float] = []
    if not isinstance(components, list) or not components:
        errors.append(f"partial-anchor source components are missing: {label}")
    else:
        for component in components:
            if (not isinstance(component, dict)
                    or set(component) != {
                        "x", "x0", "x1", "tone", "elements", "clipped"}
                    or not all(_finite_number(component.get(key)) for key in (
                        "x", "x0", "x1", "tone"))
                    or not isinstance(component.get("elements"), list)
                    or not component["elements"]
                    or not all(isinstance(item, str) and item
                               for item in component["elements"])
                    or len(component["elements"])
                    != len(set(component["elements"]))
                    or component.get("clipped") is not False):
                errors.append(
                    f"partial-anchor source component is malformed: {label}")
                continue
            x = _rounded_six(component["x"])
            x0 = _rounded_six(component["x0"])
            x1 = _rounded_six(component["x1"])
            tone = float(component["tone"])
            if (x0 >= x1 or x != _rounded_six((x0 + x1) / 2)
                    or not 0.0 <= tone <= 1.0):
                errors.append(
                    f"partial-anchor source component relation is false: "
                    f"{label}")
            component_x.append(x)
            component_tones.append(tone)
        if component_x != sorted(component_x):
            errors.append(
                f"partial-anchor source components are not ordered: {label}")
        if (any(not any(abs(component - divider)
                        <= REFEREE_POSITION_TOLERANCE_PT
                        for divider in source_values)
                for component in component_x)
                or any(not any(abs(component - divider)
                               <= REFEREE_POSITION_TOLERANCE_PT
                               for component in component_x)
                       for divider in source_values)):
            errors.append(
                f"partial-anchor components do not bind the topology: {label}")
    if component_tones and any(
            abs(tone - component_tones[0]) > 1e-8
            for tone in component_tones):
        errors.append(
            f"partial-anchor divider tone is not singular: {label}")
    divider_tone = component_tones[0] if component_tones else None

    vertical_names = (
        "y0", "y1", "contract_y0", "contract_y1", "open_y0", "open_y1",
        "contract_span_pt", "seed_span_pt", "measured_span_pt",
        "unmeasured_span_pt",
    )
    if not all(_finite_number(referee.get(name)) for name in vertical_names):
        return [*errors,
                f"partial-anchor span evidence is malformed: {label}"]
    y0, y1, contract_y0, contract_y1, open_y0, open_y1 = (
        float(referee[name]) for name in vertical_names[:6])
    contract_span = float(referee["contract_span_pt"])
    seed_span = float(referee["seed_span_pt"])
    measured_span = float(referee["measured_span_pt"])
    unmeasured_span = float(referee["unmeasured_span_pt"])
    if (not contract_y0 <= open_y0 < open_y1 <= contract_y1
            or not open_y0 <= y0 < y1 <= open_y1
            or y1 - y0 <= REFEREE_POSITION_TOLERANCE_PT
            or _rounded_six(contract_span)
            != _rounded_six(contract_y1 - contract_y0)
            or _rounded_six(seed_span) != _rounded_six(open_y1 - open_y0)
            or measured_span <= 0
            or measured_span > seed_span + epsilon
            or _rounded_six(unmeasured_span)
            != _rounded_six(max(0.0, seed_span - measured_span))):
        errors.append(
            f"partial-anchor span relation is false: {label}")
    # The absence proof is only exhaustive over the COMPLETE open band:
    # every slab measured, one topology, nothing ignored.
    if (abs(measured_span - seed_span) > epsilon
            or unmeasured_span > epsilon):
        errors.append(
            f"partial-anchor coverage is not the full band: {label}")
    if referee.get("ignored_slabs") != []:
        errors.append(
            f"partial-anchor band has ignored slabs: {label}")
    if referee.get("topology_superset_relations") != []:
        errors.append(
            f"partial-anchor topology relations are not singular: {label}")

    coverage = referee.get("topology_coverage_pt")
    chosen_key = _referee_topology_key(source_values)
    if (not isinstance(coverage, dict)
            or set(coverage) != {chosen_key}
            or not _finite_number(coverage.get(chosen_key))
            or abs(float(coverage[chosen_key]) - measured_span) > epsilon):
        errors.append(
            f"partial-anchor topology coverage is not singular: {label}")

    certificate = referee.get("active_partial_anchor_certificate")
    if (not isinstance(certificate, dict)
            or set(certificate) != PARTIAL_ANCHOR_CERTIFICATE_KEYS):
        return [*errors,
                f"partial-anchor certificate schema is unsupported: {label}"]
    fixed_relations = {
        "criterion": PARTIAL_ANCHOR_CRITERION,
        "valid": True,
        "ledger_state": "active_unresolved",
        "subject_ownership_basis": PARTIAL_ANCHOR_OWNERSHIP_BASIS,
        "independent_source_enclosure_proven": False,
        "divider_count_basis": PARTIAL_ANCHOR_COUNT_BASIS,
        "missing_anchor_basis": PARTIAL_ANCHOR_MISSING_BASIS,
        "anchor_corridor_clipped_paint_elements": [],
        "anchor_corridor_unsupported_region_elements": [],
        "open_y0": referee["open_y0"],
        "open_y1": referee["open_y1"],
        "coverage_pt": referee["measured_span_pt"],
        "source_divider_x": source_values,
        "observed_anchor_x": observed_layout,
        "missing_anchor_x": missing_values,
    }
    for key, expected in fixed_relations.items():
        if certificate.get(key) != expected:
            errors.append(
                f"partial-anchor certificate relation is false: "
                f"{label}/{key}")

    proofs = certificate.get("missing_anchor_proofs")
    if (not isinstance(proofs, list)
            or len(proofs) != len(missing_values)):
        return [*errors,
                f"partial-anchor erasure proofs are incomplete: {label}"]
    for anchor, proof in zip(missing_values, proofs):
        if not isinstance(proof, dict) or set(proof) != PARTIAL_ANCHOR_PROOF_KEYS:
            errors.append(
                f"partial-anchor erasure proof is malformed: {label}")
            continue
        if (proof.get("layout_x") != anchor
                or proof.get("open_y0") != referee["open_y0"]
                or proof.get("open_y1") != referee["open_y1"]
                or proof.get("raw_rail_identity_valid") is not True
                or proof.get("proof_top_role_ambiguities") != []
                or proof.get("clipped_paint_elements") != []
                or proof.get("final_target_tone_segments") != []
                or proof.get("unsupported_region_elements") != []):
            errors.append(
                f"partial-anchor erasure proof relation is false: {label}")
        if (not _finite_number(proof.get("corridor_x0"))
                or not _finite_number(proof.get("corridor_x1"))
                or not _finite_number(proof.get("proof_x0"))
                or not _finite_number(proof.get("proof_x1"))
                or abs(float(proof["corridor_x0"])
                       - (anchor - REFEREE_POSITION_TOLERANCE_PT)) > epsilon
                or abs(float(proof["corridor_x1"])
                       - (anchor + REFEREE_POSITION_TOLERANCE_PT)) > epsilon):
            errors.append(
                f"partial-anchor corridor geometry is false: {label}")
            continue
        rails = proof.get("raw_anchor_rails")
        if not isinstance(rails, list) or len(rails) != 1:
            # The absence proof requires exactly one raw rail whose identity
            # at the anchor is unambiguous.
            errors.append(
                f"partial-anchor raw rail is not singular: {label}")
            continue
        rail = rails[0]
        if (not isinstance(rail, dict)
                or set(rail) != PARTIAL_ANCHOR_RAIL_KEYS
                or not all(_finite_number(rail.get(key)) for key in (
                    "x0", "x1", "center_x", "delta_pt", "y0", "y1", "tone"))
                or not isinstance(rail.get("element"), str)
                or not rail["element"]
                or not isinstance(rail.get("kind"), str) or not rail["kind"]
                or not _is_count(rail.get("order"))):
            errors.append(
                f"partial-anchor raw rail is malformed: {label}")
            continue
        rail_x0 = float(rail["x0"])
        rail_x1 = float(rail["x1"])
        rail_tone = float(rail["tone"])
        if (rail_x0 >= rail_x1
                or abs(float(rail["center_x"]) - (rail_x0 + rail_x1) / 2)
                > epsilon
                or abs(float(rail["delta_pt"])
                       - (float(rail["center_x"]) - anchor)) > epsilon
                or abs(float(rail["center_x"]) - anchor)
                > REFEREE_POSITION_TOLERANCE_PT
                or rail.get("clipped") is not False
                or not 0.0 <= rail_tone <= 1.0
                or (divider_tone is not None
                    and abs(rail_tone - divider_tone) > 1e-8)):
            # The erased rail must sit at the missing anchor, unclipped, in
            # the same target tone as the observed dividers.
            errors.append(
                f"partial-anchor raw rail relation is false: {label}")
        if (float(rail["y0"]) > open_y0 + epsilon
                or float(rail["y1"]) < open_y1 - epsilon):
            errors.append(
                f"partial-anchor raw rail does not span the open band: "
                f"{label}")
        if (abs(float(proof["proof_x0"])
                - min(float(proof["corridor_x0"]), rail_x0)) > epsilon
                or abs(float(proof["proof_x1"])
                       - max(float(proof["corridor_x1"]), rail_x1))
                > epsilon):
            errors.append(
                f"partial-anchor proof window is false: {label}")

        slabs = proof.get("erasure_slabs")
        if not isinstance(slabs, list) or not slabs:
            errors.append(
                f"partial-anchor erasure slabs are missing: {label}")
            continue
        owner_roles: set[tuple[str, int, str, float]] = set()
        slabs_valid = True
        previous_y1 = open_y0
        for index, slab in enumerate(slabs):
            if (not isinstance(slab, dict)
                    or set(slab) != PARTIAL_ANCHOR_SLAB_KEYS
                    or not _finite_number(slab.get("y0"))
                    or not _finite_number(slab.get("y1"))
                    or not _finite_number(slab.get("sample_y"))):
                errors.append(
                    f"partial-anchor erasure slab is malformed: {label}")
                slabs_valid = False
                continue
            slab_y0 = float(slab["y0"])
            slab_y1 = float(slab["y1"])
            if (slab_y0 >= slab_y1
                    or abs(slab_y0 - previous_y1) > epsilon
                    or (index == len(slabs) - 1
                        and abs(slab_y1 - open_y1) > epsilon)
                    or abs(float(slab["sample_y"])
                           - (slab_y0 + slab_y1) / 2) > epsilon):
                # The slabs must tile the complete open band in order; a gap
                # would leave a stretch of the rail with no erasure evidence.
                errors.append(
                    f"partial-anchor erasure slabs do not tile the band: "
                    f"{label}")
                slabs_valid = False
            previous_y1 = slab_y1
            if (slab.get("raw_rail_elements") != [rail["element"]]
                    or slab.get("ambiguous_top_roles") != []):
                errors.append(
                    f"partial-anchor erasure slab evidence is false: {label}")
                slabs_valid = False
            intervals = slab.get("raw_intervals")
            if (not isinstance(intervals, list) or len(intervals) != 1
                    or not _finite_number_list(intervals[0], length=2)
                    or abs(float(intervals[0][0]) - rail_x0) > epsilon
                    or abs(float(intervals[0][1]) - rail_x1) > epsilon):
                errors.append(
                    f"partial-anchor raw interval is not the rail: {label}")
                slabs_valid = False
                continue
            segments = slab.get("final_owner_segments")
            if not isinstance(segments, list) or not segments:
                errors.append(
                    f"partial-anchor final owners are missing: {label}")
                slabs_valid = False
                continue
            previous_x1 = rail_x0
            slab_roles: set[tuple[str, int, str, float]] = set()
            for position, segment in enumerate(segments):
                if (not isinstance(segment, dict)
                        or set(segment) != PARTIAL_ANCHOR_SEGMENT_KEYS
                        or not _finite_number(segment.get("x0"))
                        or not _finite_number(segment.get("x1"))
                        or not _finite_number(segment.get("tone"))
                        or not isinstance(segment.get("element"), str)
                        or not segment["element"]
                        or not isinstance(segment.get("kind"), str)
                        or not segment["kind"]
                        or not _is_count(segment.get("order"))):
                    errors.append(
                        f"partial-anchor final owner is malformed: {label}")
                    slabs_valid = False
                    continue
                segment_x0 = float(segment["x0"])
                segment_x1 = float(segment["x1"])
                if (segment_x0 >= segment_x1
                        or abs(segment_x0 - previous_x1) > epsilon
                        or (position == len(segments) - 1
                            and abs(segment_x1 - rail_x1) > epsilon)):
                    # The final owners must tile the raw rail exhaustively;
                    # an uncovered sliver means the rail reaches paper.
                    errors.append(
                        f"partial-anchor erasure does not cover the rail: "
                        f"{label}")
                    slabs_valid = False
                previous_x1 = segment_x1
                if (segment.get("clipped") is not False
                        or abs(float(segment["tone"]) - rail_tone) <= 1e-8
                        or int(segment["order"]) <= int(rail["order"])):
                    # A clipped, target-tone, or underpainted owner does not
                    # erase the rail.
                    errors.append(
                        f"partial-anchor final owner does not erase the "
                        f"rail: {label}")
                    slabs_valid = False
                slab_roles.add((
                    segment["element"], int(segment["order"]),
                    segment["kind"], float(segment["tone"])))
            if len(slab_roles) != 1:
                errors.append(
                    f"partial-anchor final owner is not singular: {label}")
                slabs_valid = False
            owner_roles.update(slab_roles)
        if slabs_valid and len(owner_roles) != 1:
            errors.append(
                f"partial-anchor final owner is not singular: {label}")
        expected_roles = [
            {
                "element": role[0], "order": role[1],
                "kind": role[2], "tone": role[3],
            }
            for role in sorted(owner_roles)
        ]
        if slabs_valid and proof.get("erasure_owner_roles") != expected_roles:
            errors.append(
                f"partial-anchor owner roles are not derived: {label}")
    return errors


def _project_layout_topology(
        comb: Any, bbox: list[float], label: str,
        ) -> dict[str, Any]:
    """Project exactly the topology digest published by comb_referee.py."""
    if not isinstance(comb, dict):
        raise CombRefereeScopeError(f"{label} has no comb topology")
    cells = comb.get("cells")
    divider_count = comb.get("divider_count")
    raw_dividers = comb.get("divider_x")
    raw_slots = comb.get("slot_x")
    if (not _is_count(cells) or cells < 1
            or divider_count != cells - 1
            or not _finite_number_list(raw_dividers, length=cells - 1)
            or not _finite_number_list(raw_slots, length=cells + 1)):
        raise CombRefereeScopeError(f"{label} has invalid comb counts/edges")
    dividers = [float(value) for value in raw_dividers]
    slots = [float(value) for value in raw_slots]
    # The outer values of slot_x are the comb's own printed RAILS, and they are
    # not the subject rectangle. That rectangle's x is a fused lattice position
    # -- the mean centre of every collinear bar on the line -- while the rail is
    # the bar crossing this band; and one rectangle may rule a caption or a
    # dash box beside the comb, which the comb does not own. What must hold is
    # that every COMPARTMENT is this subject's, i.e. that its centre lies
    # inside the rectangle; a compartment centred outside it belongs to the
    # subject next door.
    if (any(right <= left for left, right in zip(slots, slots[1:]))
            or not _same_finite_numbers(slots[1:-1], dividers)
            or not all(bbox[0] < (left + right) / 2.0 < bbox[2]
                       for left, right in zip(slots, slots[1:]))):
        raise CombRefereeScopeError(f"{label} comb edges are inconsistent")
    y0 = comb.get("y0")
    y1 = comb.get("y1")
    pitch = comb.get("pitch_pt")
    resolution = comb.get("resolution")
    if (not _finite_number(y0) or not _finite_number(y1)
            or float(y1) <= float(y0)
            or not _finite_number(pitch) or float(pitch) <= 0
            or not isinstance(resolution, dict)):
        raise CombRefereeScopeError(f"{label} comb band is invalid")
    resolution_status = resolution.get("status")
    reason_codes = resolution.get("reason_codes")
    if (resolution_status not in {"resolved", "unresolved"}
            or not _string_list(reason_codes)
            or bool(reason_codes) != (resolution_status == "unresolved")):
        raise CombRefereeScopeError(f"{label} comb resolution is invalid")
    topology = {
        "cells": cells,
        "divider_x": dividers,
        "slot_x": slots,
        "y0": float(y0),
        "y1": float(y1),
        "resolution_status": resolution_status,
        "reason_codes": reason_codes,
    }
    topology["sha256"] = canonical_digest(topology)
    return topology


def _emission_geometry_from_layout(
        page_index: int, cell: dict[str, Any], box: dict[str, float],
        ) -> dict[str, Any]:
    comb = cell["comb"]
    slot_x = [float(value) for value in comb["slot_x"]]
    left = float(box["x0"])
    top = float(box["y0"])
    right = float(box["x1"])
    bottom = float(box["y1"])
    # The writing surface, not the guide-tick band.  comb["y0"]/["y1"] is the
    # short tick the source paints at the cell's foot (~2.88pt tall); a typed
    # character goes on comb["writing_y0"]/["writing_y1"], which is what emit
    # renders.  Subscript, not .get(): a layout missing the writing band is an
    # error here, never a silently tolerated pass.
    band_top = float(comb["writing_y0"])
    band_bottom = float(comb["writing_y1"])
    # The same distinction horizontally, and subscripted for the same reason.
    # comb["slot_x"]'s outer values are the RAIL CENTRES -- half a printed
    # stroke outside the paper -- and comb["writing_x0"]/["writing_x1"] are
    # those rails' ink edges, which is what emit lays the first and last
    # compartments on.  Everything between them stays the measured dividers.
    edges = [float(comb["writing_x0"]), *slot_x[1:-1],
             float(comb["writing_x1"])]
    return {
        "page_index": page_index,
        "left": left,
        "top": top,
        "width": right - left,
        "height": bottom - top,
        "slots": [
            {
                "index": index,
                "left": slot_left - left,
                "top": band_top - top,
                "width": slot_right - slot_left,
                "height": band_bottom - band_top,
            }
            for index, (slot_left, slot_right) in enumerate(
                zip(edges, edges[1:]))
        ],
    }


def _layout_subject_sort_key(
        item: tuple[str, dict[str, Any]],
        ) -> tuple[int, int, str]:
    """Order projected subjects by the layout's own cell stream.

    A cell id is a CONTINUITY identifier, not a geometric one.  lattice.py
    keeps a cell's legacy id when its ``subject_key`` still matches a legacy
    box and otherwise draws a fresh id starting at ``len(legacy_boxes)``, so a
    cell created after a partition repair carries a number far above its
    neighbours while sitting geometrically among them (2550M's restored comb
    owner ``p1c193`` is emitted between ``p1c97`` and ``p1c103``).  Parsing
    ``p<page>c<n>`` therefore reads discovery history, not document order, and
    is only ever right by luck: two reconciliations three days apart -- the
    producer aligned to the cell stream, this projection aligned to the
    referee's numeric key -- picked opposite canonical orders and agreed on
    every form only because no form had yet grown a late-created owner.

    The canonical order is the layout cell stream, which every producer
    already declares (lattice.py's ledger note, audit.py's offender
    publication, audit.py's retained-partition check).  ``stream_index`` is
    recorded by ``_layout_binding_projection`` while it walks ``page["cells"]``
    and is a plain int in the persisted binding, so this key survives the
    ``sort_keys=True`` JSON round trip that both the referee envelope and the
    audit envelope apply to the registry.
    """
    cell_id, cell = item
    page = cell.get("page") if isinstance(cell, dict) else None
    stream_index = cell.get("stream_index") if isinstance(cell, dict) else None
    return (
        page if isinstance(page, int) and not isinstance(page, bool)
        else sys.maxsize,
        stream_index if isinstance(stream_index, int)
        and not isinstance(stream_index, bool) else sys.maxsize,
        cell_id,
    )


def _ordered_layout_cell_items(
        cells: Any,
        ) -> list[tuple[str, dict[str, Any]]]:
    """Return layout cells in the canonical order despite JSON key sorting."""
    if not isinstance(cells, dict):
        return []
    try:
        return sorted(cells.items(), key=_layout_subject_sort_key)
    except (TypeError, ValueError):
        # The caller will report the malformed cell itself.  Preserve a
        # non-crashing path so hostile evidence remains UNEVALUABLE.
        return list(cells.items())


def _layout_binding_projection(
        slug: str, layout: Any, guide: Any,
        lattice_record: dict[str, Any], layout_sha256: str,
        guide_sha256: str,
        ) -> dict[str, Any]:
    """Bind every deterministic child ledger claim to parsed layout bytes."""
    if not isinstance(layout, dict) or not isinstance(layout.get("pages"), list):
        raise CombRefereeScopeError(f"layout projection is malformed: {slug}")
    if not isinstance(guide, dict):
        raise CombRefereeScopeError(f"guide projection is malformed: {slug}")
    relocated: set[str] = set()
    clipped: dict[str, dict[str, float]] = {}
    for region in guide.get("inline") or []:
        if not isinstance(region, dict):
            raise CombRefereeScopeError(f"guide inline region is malformed: {slug}")
        cell_ids = region.get("cell_ids") or []
        if (not isinstance(cell_ids, list)
                or not all(isinstance(item, str) for item in cell_ids)):
            raise CombRefereeScopeError(f"guide relocation list is malformed: {slug}")
        relocated.update(cell_ids)
        for straddler in region.get("straddlers") or []:
            if (not isinstance(straddler, dict)
                    or straddler.get("kind") != "cell"
                    or straddler.get("disposition") != "clipped"):
                continue
            cell_id = straddler.get("ref")
            form_box = straddler.get("form")
            if (not isinstance(cell_id, str) or cell_id in clipped
                    or not isinstance(form_box, dict)
                    or any(not _finite_number(form_box.get(name))
                           for name in ("x0", "y0", "x1", "y1"))):
                raise CombRefereeScopeError(
                    f"guide clipped-cell evidence is malformed: {slug}")
            clipped[cell_id] = {
                name: float(form_box[name])
                for name in ("x0", "y0", "x1", "y1")
            }

    projected_cells: dict[str, Any] = {}
    projected_inferences: dict[str, Any] = {}
    audit_expected_ids: list[str] = []
    for expected_page, page in enumerate(layout["pages"], 1):
        if (not isinstance(page, dict)
                or page.get("index") != expected_page
                or not isinstance(page.get("cells"), list)
                or not isinstance(page.get("comb_subjects"), list)
                or not isinstance(page.get("comb_inferences"), list)):
            raise CombRefereeScopeError(
                f"layout ledger page is incomplete: {slug}/p{expected_page}")
        cells_by_id: dict[str, dict[str, Any]] = {}
        # The position of a cell in page["cells"] is the only geometric order
        # the layout carries; the cell's id is a continuity identifier that
        # survives across regenerations and says nothing about where the cell
        # sits (see _layout_subject_sort_key).  Capture the stream position
        # here, on the single walk that already establishes cells_by_id, so
        # every consumer of this projection can order subjects the way the
        # layout, the audit and the emission all publish them.
        stream_index_by_id: dict[str, int] = {}
        for stream_index, raw_cell in enumerate(page["cells"]):
            if not isinstance(raw_cell, dict) or not isinstance(
                    raw_cell.get("id"), str):
                raise CombRefereeScopeError(
                    f"layout cell is malformed: {slug}/p{expected_page}")
            cell_id = raw_cell["id"]
            if cell_id in cells_by_id:
                raise CombRefereeScopeError(
                    f"layout cell is duplicated: {slug}/{cell_id}")
            cells_by_id[cell_id] = raw_cell
            stream_index_by_id[cell_id] = stream_index
            if isinstance(raw_cell.get("comb"), dict) and cell_id not in relocated:
                audit_expected_ids.append(cell_id)
        # A retained legacy box has no cell in the current stream -- that is
        # what "retained_unresolved" means -- so it has no document position.
        # Rank those after every streamed cell on their page, in the order the
        # reviewed ledger lists them, which is where lattice.py already emits
        # them.  Nothing is derived from their legacy number.
        retained_stream_index = len(page["cells"])

        for subject in page["comb_subjects"]:
            if not isinstance(subject, dict):
                raise CombRefereeScopeError(
                    f"layout subject is malformed: {slug}/p{expected_page}")
            state = subject.get("state")
            subject_key = subject.get("subject_key")
            legacy_id = subject.get("legacy_cell_id")
            active_id = subject.get("cell_id")
            bbox_raw = subject.get("legacy_bbox")
            reason_codes = subject.get("reason_codes")
            blocks_gate = subject.get("blocks_gate")
            # A reviewed composite keeps its reason codes (they say WHY the
            # comb was suppressed, which the review confirmed rather than
            # erased) but stops blocking, on a certificate this gate
            # re-derives corpus-wide. Enumerated with every other site
            # carrying this contract rather than patched where it surfaced.
            suppressed_shape = state in LEDGER_SUPPRESSED_STATES
            if (state not in LEDGER_STATES
                    or not isinstance(subject_key, str) or not subject_key
                    or not isinstance(legacy_id, str) or not legacy_id
                    or not _finite_number_list(bbox_raw, length=4)
                    or not _string_list(
                        reason_codes, nonempty=state != "active_resolved")
                    or blocks_gate is not (state not in LEDGER_NONBLOCKING_STATES)):
                raise CombRefereeScopeError(
                    f"layout subject relation is malformed: {slug}/{legacy_id}")
            bbox = [float(value) for value in bbox_raw]
            if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                raise CombRefereeScopeError(
                    f"layout subject bbox is invalid: {slug}/{legacy_id}")
            if suppressed_shape:
                if active_id is not None:
                    raise CombRefereeScopeError(
                        f"retained subject has active id: {slug}/{legacy_id}")
                report_id = legacy_id
                comb = subject.get("legacy_comb")
                emission_geometry = None
                subject_stream_index = retained_stream_index
                retained_stream_index += 1
            else:
                if not isinstance(active_id, str) or active_id not in cells_by_id:
                    raise CombRefereeScopeError(
                        f"active subject has no layout owner: {slug}/{legacy_id}")
                owner = cells_by_id[active_id]
                owner_bbox = [owner.get(name) for name in ("x0", "y0", "x1", "y1")]
                if (owner.get("subject_key") != subject_key
                        or not _same_finite_numbers(owner_bbox, bbox)):
                    raise CombRefereeScopeError(
                        f"active subject owner relation is false: {slug}/{active_id}")
                report_id = active_id
                subject_stream_index = stream_index_by_id[active_id]
                comb = owner.get("comb")
                form_box = clipped.get(active_id, {
                    name: float(owner[name])
                    for name in ("x0", "y0", "x1", "y1")
                })
                emission_geometry = (
                    None if active_id in relocated else
                    _emission_geometry_from_layout(expected_page, owner, form_box)
                )
            topology = _project_layout_topology(
                comb, bbox, f"{slug}/{report_id}")
            if report_id in projected_cells:
                raise CombRefereeScopeError(
                    f"layout report subject is duplicated: {slug}/{report_id}")
            projected_cells[report_id] = {
                "cell": report_id,
                "subject_key": subject_key,
                "legacy_cell_id": legacy_id,
                "cell_id": active_id,
                "ledger_state": state,
                "ledger_blocks_gate": blocks_gate,
                "ledger_reason_codes": reason_codes,
                "ledger_topology_sha256": topology["sha256"],
                "ledger_evidence": subject,
                "page": expected_page,
                "stream_index": subject_stream_index,
                "bbox": bbox,
                "latticed": topology["cells"],
                "lattice_divider_x": topology["divider_x"],
                "expected_emission_geometry": emission_geometry,
            }

        for inference in page["comb_inferences"]:
            if not isinstance(inference, dict):
                raise CombRefereeScopeError(
                    f"layout inference is malformed: {slug}/p{expected_page}")
            inference_id = inference.get("cell_id")
            subject_key = inference.get("subject_key")
            bbox_raw = inference.get("bbox")
            if (not isinstance(inference_id, str) or not inference_id
                    or inference_id in projected_inferences
                    or not isinstance(subject_key, str) or not subject_key
                    or inference.get("state") != INFERENCE_STATE
                    or inference.get("blocks_gate") is not True
                    or not _string_list(
                        inference.get("reason_codes"), nonempty=True)
                    or not _finite_number_list(bbox_raw, length=4)):
                raise CombRefereeScopeError(
                    f"layout inference relation is malformed: {slug}/{inference_id}")
            bbox = [float(value) for value in bbox_raw]
            topology = _project_layout_topology(
                inference.get("inferred_comb"), bbox,
                f"{slug}/{inference_id} inference")
            projected_inferences[inference_id] = {
                "page": expected_page,
                "subject_key": subject_key,
                "cell_id": inference_id,
                "state": INFERENCE_STATE,
                "blocks_gate": True,
                "reason_codes": inference["reason_codes"],
                "bbox": bbox,
                "topology_sha256": topology["sha256"],
                "ledger_evidence": inference,
            }

    generator = layout.get("generator")
    if not isinstance(generator, dict):
        raise CombRefereeScopeError(f"layout generator is missing: {slug}")
    lattice_evidence = {
        "file": "tools/formgen/lattice.py",
        "bytes": lattice_record.get("bytes"),
        "sha256": lattice_record.get("sha256"),
        "expected_sha256": lattice_record.get("sha256"),
        "layout_generator": generator,
    }
    ordered_cells = dict(sorted(
        projected_cells.items(), key=_layout_subject_sort_key))
    result = {
        "layout_sha256": layout_sha256,
        "guide_sha256": guide_sha256,
        "lattice_evidence": lattice_evidence,
        "audit_expected_ids": audit_expected_ids,
        "cells": ordered_cells,
        "inferences": projected_inferences,
    }
    _layout_audit_owner_ids(result)
    return result


def _layout_binding_snapshots(
        layout_tree: dict[str, Any], guide_tree: dict[str, Any],
        lattice_record: dict[str, Any],
        ) -> dict[str, Any]:
    layout_files = _manifest_files(layout_tree)
    guide_files = _manifest_files(guide_tree)
    result: dict[str, Any] = {}
    for logical, layout_record in sorted(layout_files.items()):
        if not logical.endswith(".layout.json"):
            continue
        slug = pathlib.PurePosixPath(logical).name.removesuffix(".layout.json")
        guide_logical = f"build/guides/{slug}.guide.json"
        guide_record = guide_files.get(guide_logical)
        if guide_record is None:
            raise CombRefereeScopeError(f"guide is missing for layout: {slug}")
        layout_path = BUILD / "layout" / f"{slug}.layout.json"
        guide_path = BUILD / "guides" / f"{slug}.guide.json"
        if (_stable_file_record(layout_path, logical) != layout_record
                or _stable_file_record(guide_path, guide_logical) != guide_record):
            raise CombRefereeScopeError(
                f"layout/guide changed while projecting ledger: {slug}")
        try:
            layout = json.loads(layout_path.read_text(encoding="utf-8"))
            guide = json.loads(guide_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, RecursionError) as error:
            raise CombRefereeScopeError(
                f"cannot parse layout/guide projection for {slug}: {error}") from error
        result[slug] = _layout_binding_projection(
            slug, layout, guide, lattice_record,
            layout_record["sha256"], guide_record["sha256"])
    if len(result) != EXPECTED_FORMS:
        raise CombRefereeScopeError(
            f"layout binding corpus has {len(result)}/{EXPECTED_FORMS} forms")
    return result


def _manifest_files(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    files = manifest.get("files")
    if not isinstance(files, list):
        return {}
    return {
        item["path"]: item for item in files
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }


EMITTED_EVIDENCE_KEYS = {
    "count", "indexes", "editable_indexes", "declared_capacity",
    "declared_count", "page_index", "container_position",
    "container_geometry", "layout_binding_valid", "expected_geometry",
    "slot_geometry", "valid",
}


def _emitted_evidence_binding_errors(
        slug: str, cell: dict[str, Any], expected: dict[str, Any],
        ) -> list[str]:
    errors: list[str] = []
    cell_id = cell.get("cell")
    evidence = cell.get("emitted_evidence")
    expected_geometry = expected.get("expected_emission_geometry")
    if expected_geometry is None:
        if (evidence is not None or cell.get("emitted") is not None
                or cell.get("emitted_indexes_valid") is not False):
            errors.append(
                f"suppressed cell has fabricated emission: {slug}/{cell_id}")
        return errors
    if not isinstance(evidence, dict) or set(evidence) != EMITTED_EVIDENCE_KEYS:
        return [f"cell emitted evidence schema is unsupported: {slug}/{cell_id}"]
    count = evidence.get("count")
    indexes = evidence.get("indexes")
    editable = evidence.get("editable_indexes")
    if (not _is_count(count)
            or not isinstance(indexes, list)
            or not all(_is_count(index) for index in indexes)
            or not isinstance(editable, list)
            or not all(_is_count(index) for index in editable)
            or len(indexes) != len(set(indexes))
            or len(editable) != len(set(editable))):
        return [f"cell emitted index evidence is malformed: {slug}/{cell_id}"]
    if (cell.get("emitted") != count
            or cell.get("emitted_indexes_valid") is not evidence.get("valid")):
        errors.append(f"cell emitted summary is false: {slug}/{cell_id}")
    if (evidence.get("expected_geometry") != expected_geometry
            or evidence.get("page_index") != expected_geometry["page_index"]):
        errors.append(f"cell expected emission geometry is unbound: {slug}/{cell_id}")
    position = evidence.get("container_position")
    geometry = evidence.get("container_geometry")
    slot_geometry = evidence.get("slot_geometry")
    expected_slots = expected_geometry["slots"]
    actual_container = (
        [*position, *geometry]
        if _finite_number_list(position, length=2)
        and _finite_number_list(geometry, length=2) else None)
    expected_container = [
        expected_geometry["left"], expected_geometry["top"],
        expected_geometry["width"], expected_geometry["height"],
    ]
    container_matches = bool(
        actual_container is not None
        and all(abs(float(actual) - float(target))
                <= HTML_GEOMETRY_EPSILON_PT
                for actual, target in zip(actual_container, expected_container)))
    slots_match = bool(
        isinstance(slot_geometry, list)
        and len(slot_geometry) == len(expected_slots)
        and all(
            isinstance(actual, dict)
            and set(actual) == {"index", "left", "top", "width", "height"}
            and actual.get("index") == target["index"]
            and all(_finite_number(actual.get(name))
                    and abs(float(actual[name]) - float(target[name]))
                    <= HTML_GEOMETRY_EPSILON_PT
                    for name in ("left", "top", "width", "height"))
            for actual, target in zip(slot_geometry, expected_slots)
        ))
    expected_layout_binding = container_matches and slots_match
    if evidence.get("layout_binding_valid") is not expected_layout_binding:
        errors.append(f"cell layout-binding verdict is false: {slug}/{cell_id}")
    if evidence.get("valid") is True:
        expected_count = expected.get("latticed")
        if (count != expected_count
                or indexes != list(range(count))
                or evidence.get("declared_capacity") != count
                or evidence.get("declared_count") != count
                or not all(index in set(indexes) for index in editable)
                or not expected_layout_binding):
            errors.append(f"cell valid-emission claim is false: {slug}/{cell_id}")
    elif evidence.get("valid") is not False:
        errors.append(f"cell emitted validity is not boolean: {slug}/{cell_id}")
    return errors


def form_binding_errors(form: dict[str, Any],
                        snapshot: dict[str, Any]) -> list[str]:
    """Bind every per-form claim to bytes in the outer immutable snapshot."""
    errors: list[str] = []
    slug = form.get("slug")
    if not isinstance(slug, str):
        return ["form binding has no slug"]
    artifacts = form.get("artifacts")
    source = form.get("source")
    if not isinstance(artifacts, dict) or set(artifacts) != FORM_ARTIFACT_KEYS:
        return [f"form artifact schema is incomplete: {slug}"]
    if not isinstance(source, dict) or set(source) != FORM_SOURCE_KEYS:
        return [f"form source schema is incomplete: {slug}"]

    trees = snapshot.get("artifact_trees", {})
    expected_artifacts = {
        "ir_sha256": ("ir", f"build/ir/{slug}.ir.json"),
        "layout_sha256": ("layout", f"build/layout/{slug}.layout.json"),
        "html_sha256": ("html", f"build/html/{slug}.html"),
        "guide_sha256": ("guides", f"build/guides/{slug}.guide.json"),
    }
    for field, (tree_name, logical) in expected_artifacts.items():
        tree = trees.get(tree_name, {}) if isinstance(trees, dict) else {}
        record = _manifest_files(tree).get(logical)
        if record is None or artifacts.get(field) != record.get("sha256"):
            errors.append(f"form artifact is not bound: {slug}/{field}")
    layout_bindings = snapshot.get("layout_bindings")
    layout_binding = layout_bindings.get(slug) if isinstance(
        layout_bindings, dict) else None
    layout_owner_ids: list[str] | None = None
    if (not isinstance(layout_binding, dict)
            or layout_binding.get("layout_sha256")
            != artifacts.get("layout_sha256")
            or layout_binding.get("guide_sha256")
            != artifacts.get("guide_sha256")):
        errors.append(f"form parsed layout binding is missing: {slug}")
        layout_binding = None
    elif form.get("lattice_evidence") != layout_binding.get(
            "lattice_evidence"):
        errors.append(f"form lattice producer/layout binding is false: {slug}")
    else:
        try:
            layout_owner_ids = _layout_audit_owner_ids(layout_binding)
        except CombRefereeScopeError as error:
            errors.append(f"form parsed owner registry is invalid: {slug}: {error}")
    if artifacts.get("html_structure_sha256") != artifacts.get("html_sha256"):
        errors.append(f"form HTML structure digest is not byte-exact: {slug}")
    guide_logical = f"build/html/{slug}.guide.html"
    guide_record = _manifest_files(
        trees.get("html", {}) if isinstance(trees, dict) else {}
    ).get(guide_logical)
    expected_guide_sha = guide_record.get("sha256") if guide_record else None
    if artifacts.get("guide_html_sha256") != expected_guide_sha:
        errors.append(f"form optional guide HTML is not bound: {slug}")

    if layout_binding is not None:
        expected_cells = layout_binding.get("cells")
        expected_inferences = layout_binding.get("inferences")
        report_cells = form.get("cells")
        report_inferences = form.get("inferences")
        if (not isinstance(expected_cells, dict)
                or not isinstance(report_cells, list)):
            errors.append(f"form layout/report cell inventory is malformed: {slug}")
        else:
            ordered_expected_cells = dict(
                _ordered_layout_cell_items(expected_cells))
            report_ids = [
                cell.get("cell") if isinstance(cell, dict) else None
                for cell in report_cells
            ]
            report_by_id = {
                cell.get("cell"): cell for cell in report_cells
                if isinstance(cell, dict) and isinstance(cell.get("cell"), str)
            }
            if (len(report_by_id) != len(report_cells)
                    or set(report_by_id) != set(expected_cells)
                    or report_ids != list(ordered_expected_cells)):
                errors.append(
                    f"form report/layout subject inventory differs: {slug}")
            if layout_owner_ids is not None:
                report_owner_ids = [
                    cell_id for cell_id in report_ids
                    if isinstance(cell_id, str)
                    and isinstance(expected_cells.get(cell_id), dict)
                    and expected_cells[cell_id].get(
                        "expected_emission_geometry") is not None
                ]
                if report_owner_ids != layout_owner_ids:
                    errors.append(
                        f"form emitted owner order differs from layout: {slug}")
            deterministic_fields = (
                "cell", "subject_key", "legacy_cell_id", "cell_id",
                "ledger_state", "ledger_blocks_gate", "ledger_reason_codes",
                "ledger_topology_sha256", "ledger_evidence", "page", "bbox",
                "latticed", "lattice_divider_x",
            )
            for cell_id, expected_cell in ordered_expected_cells.items():
                actual = report_by_id.get(cell_id)
                if not isinstance(actual, dict):
                    continue
                for field in deterministic_fields:
                    if actual.get(field) != expected_cell.get(field):
                        errors.append(
                            f"cell layout/ledger binding is false: "
                            f"{slug}/{cell_id}/{field}")
                errors.extend(_emitted_evidence_binding_errors(
                    slug, actual, expected_cell))
        if (not isinstance(expected_inferences, dict)
                or not isinstance(report_inferences, list)):
            errors.append(f"form inference inventory is malformed: {slug}")
        else:
            inference_by_id = {
                inference.get("cell_id"): inference
                for inference in report_inferences
                if isinstance(inference, dict)
                and isinstance(inference.get("cell_id"), str)
            }
            if (len(inference_by_id) != len(report_inferences)
                    or set(inference_by_id) != set(expected_inferences)):
                errors.append(
                    f"form report/layout inference inventory differs: {slug}")
            for cell_id, expected_inference in expected_inferences.items():
                actual = inference_by_id.get(cell_id)
                if not isinstance(actual, dict):
                    continue
                for field, expected_value in expected_inference.items():
                    if actual.get(field) != expected_value:
                        errors.append(
                            f"inference layout/ledger binding is false: "
                            f"{slug}/{cell_id}/{field}")
                if actual.get("emitted_evidence") is not None:
                    errors.append(
                        f"suppressed inference has emitted evidence: "
                        f"{slug}/{cell_id}")
        inventory = form.get("emission_inventory")
        if isinstance(inventory, dict) and inventory.get("complete") is True:
            active_ids = sorted(
                cell_id for cell_id, expected_cell in expected_cells.items()
                if expected_cell.get("ledger_state") not in LEDGER_SUPPRESSED_STATES)
            exact_inventory = {
                "complete": True,
                "reason": "complete",
                "expected_active_cell_ids": active_ids,
                "emitted_cell_ids": active_ids,
                "missing_active_cell_ids": [],
                "unexpected_emitted_cell_ids": [],
                "retained_emitted_cell_ids": [],
                "inference_emitted_cell_ids": [],
                "invalid_active_cell_ids": [],
            }
            if inventory != exact_inventory:
                errors.append(
                    f"form complete emission inventory is not derived: {slug}")

    provenance_records = _manifest_files(snapshot.get("provenance", {}))
    provenance_file = artifacts.get("tracked_provenance_file")
    provenance_record = provenance_records.get(provenance_file)
    if (not isinstance(provenance_file, str)
            or provenance_record is None
            or artifacts.get("tracked_provenance_sha256")
            != provenance_record.get("sha256")
            or provenance_record.get("equals_head") is not True):
        errors.append(f"form tracked provenance is not bound: {slug}")

    source_manifest = snapshot.get("source_pdfs", {})
    source_relations = source_manifest.get("relations", []) if isinstance(
        source_manifest, dict) else []
    if (not isinstance(source_relations, list)
            or source_manifest.get("relation_count") != len(source_relations)
            or source_manifest.get("candidate_file_count")
            != sum(
                relation.get("candidate_count", -1)
                for relation in source_relations
                if isinstance(relation, dict))
            or source_manifest.get("sha256")
            != canonical_digest(source_relations)):
        errors.append("source PDF manifest relation is invalid")
    source_by_slug: dict[str, Any] = {}
    if isinstance(source_relations, list):
        for relation in source_relations:
            relation_slug = relation.get("slug") if isinstance(
                relation, dict) else None
            if (not isinstance(relation_slug, str)
                    or relation_slug in source_by_slug):
                errors.append("source PDF relation inventory is duplicated")
                continue
            source_by_slug[relation_slug] = relation
    source_relation = source_by_slug.get(slug)
    if (not isinstance(source_relation, dict)
            or source.get("file") != source_relation.get("selected")
            or source.get("sha256") != source_relation.get("declared_sha256")
            or source.get("bytes") != source_relation.get("declared_bytes")
            or source.get("layout_pin") != source_relation.get("layout_pin")
            or source.get("page_count")
            != (source_relation.get("layout_pin") or {}).get("page_count")):
        errors.append(f"form source PDF pin/bytes are not bound: {slug}")
    else:
        candidates = source_relation.get("candidates")
        authoritative = [
            candidate for candidate in candidates
            if isinstance(candidate, dict)
            and candidate.get("sha256") == source_relation.get("declared_sha256")
            and candidate.get("bytes") == source_relation.get("declared_bytes")
        ] if isinstance(candidates, list) else []
        if (not isinstance(candidates, list)
                or source_relation.get("candidate_count") != len(candidates)
                or source_relation.get("matching_count") != 1
                or len(authoritative) != 1
                or authoritative[0].get("path")
                != source_relation.get("selected")
                or authoritative[0].get("sha256") != source.get("sha256")
                or authoritative[0].get("bytes") != source.get("bytes")):
            errors.append(f"form selected source PDF bytes are not bound: {slug}")

    audit_forms = snapshot.get("audit", {}).get("forms", {})
    audit_relation = audit_forms.get(slug) if isinstance(audit_forms, dict) else None
    if not isinstance(audit_relation, dict):
        errors.append(f"form has no outer audit relation: {slug}")
        return errors
    inputs = audit_relation.get("inputs")
    if not isinstance(inputs, dict):
        errors.append(f"form audit input manifest is malformed: {slug}")
        return errors
    input_expectations = {
        "ir": (f"{slug}.ir.json", True, artifacts.get("ir_sha256"),
               _manifest_files(trees.get("ir", {})).get(
                   f"build/ir/{slug}.ir.json")),
        "layout": (f"{slug}.layout.json", True, artifacts.get("layout_sha256"),
                   _manifest_files(trees.get("layout", {})).get(
                       f"build/layout/{slug}.layout.json")),
        "html": (f"{slug}.html", True, artifacts.get("html_sha256"),
                 _manifest_files(trees.get("html", {})).get(
                     f"build/html/{slug}.html")),
        "guide": (f"{slug}.guide.json", True, artifacts.get("guide_sha256"),
                  _manifest_files(trees.get("guides", {})).get(
                      f"build/guides/{slug}.guide.json")),
        "guide_html": (f"{slug}.guide.html", False, expected_guide_sha,
                       guide_record),
    }
    for role, (filename, required, digest, record) in input_expectations.items():
        present = record is not None
        expected_entry = {
            "file": filename,
            "required": required,
            "present": present,
            "bytes": record.get("bytes") if present else None,
            "sha256": digest if present else None,
        }
        if inputs.get(role) != expected_entry:
            errors.append(f"form audit input is not byte-bound: {slug}/{role}")
    if isinstance(source_relation, dict):
        expected_source_input = {
            "file": source_relation.get("declared_file"),
            "logical_identity": (
                source_relation.get("layout_pin") or {}).get("file"),
            "path": source_relation.get("selected"),
            "required": True,
            "present": True,
            "bytes": source.get("bytes"),
            "sha256": source.get("sha256"),
            "expected_sha256": source.get("sha256"),
        }
        if inputs.get("source_pdf") != expected_source_input:
            errors.append(f"form audit source input is not byte-bound: {slug}")

    audit_evidence = form.get("audit_evidence")
    if not isinstance(audit_evidence, dict):
        errors.append(f"form audit evidence is missing: {slug}")
        return errors
    assertion_relation = audit_relation.get("assertion_relation")
    if not isinstance(assertion_relation, dict):
        errors.append(f"outer audit assertion relation is missing: {slug}")
    else:
        if (layout_binding is not None
                and assertion_relation.get("expected_comb_ids")
                != layout_binding.get("audit_expected_ids")):
            errors.append(
                f"outer audit/layout comb inventory is not bound: {slug}")
        for key, expected in assertion_relation.items():
            if audit_evidence.get(key) != expected:
                errors.append(f"form audit assertion is not bound: {slug}/{key}")
        offender_dimensions = assertion_relation.get("offender_dimensions")
        cells = form.get("cells")
        if isinstance(offender_dimensions, dict) and isinstance(cells, list):
            expected_ids = set(assertion_relation.get("expected_comb_ids", []))
            unexpected_ids = set(assertion_relation.get(
                "unexpected_emitted_comb_ids", []))
            for offender_id, offender in offender_dimensions.items():
                kinds = set(offender.get("failure_kinds", [])) if isinstance(
                    offender, dict) else set()
                relation_name = offender.get("layout_relation") if isinstance(
                    offender, dict) else None
                owned = (
                    offender_id in expected_ids
                    or (offender_id in unexpected_ids
                        and "unexpected-emitted-comb" in kinds)
                    or ("emitted-cell-binding-invalid" in kinds
                        and relation_name == "cell-binding-invalid")
                    or ("unowned-live-comb-markup" in kinds
                        and relation_name == "not-owned")
                    or (offender_id == "<comb-inventory>"
                        and "comb-inventory-mismatch" in kinds)
                    or (offender_id == "<comb-owner-registry>"
                        and relation_name == "registry-invalid"
                        and "comb-owner-registry-invalid" in kinds)
                )
                if not owned:
                    errors.append(
                        f"outer audit offender is orphaned: "
                        f"{slug}/{offender_id}")
            for cell in cells:
                if not isinstance(cell, dict):
                    continue
                cell_id = cell.get("cell")
                offender = offender_dimensions.get(cell_id)
                ledger_state = cell.get("ledger_state")
                if offender is not None:
                    expected_audit = (
                        offender.get("printed"), "published-offender")
                elif (audit_evidence.get("complete") is True
                      and ledger_state in {
                          "active_resolved", "active_unresolved"}):
                    expected_audit = (
                        cell.get("latticed"), "complete-non-offender")
                elif audit_evidence.get("complete") is True:
                    expected_audit = (None, "complete-blocked-subject")
                else:
                    expected_audit = (None, "unknown-truncated")
                actual_audit = (
                    cell.get("audit_printed"), cell.get("audit_relation"))
                if actual_audit != expected_audit:
                    errors.append(
                        f"cell audit relation is not bound: {slug}/{cell_id}")
        # Three clauses below read a SHAPE the audit does not publish for a
        # holding assertion, and until the runtime attestation landed they
        # could never run: `audit_evidence["complete"]` was False on every
        # form, so this branch was dead code that had never once been
        # evaluated. The moment it went live it fired on all 53 forms,
        # including every form whose comb assertion holds with each counter
        # clean.
        #
        #   * `offender_count` and `offender_dimensions` are set by `broken()`
        #     and NOT by `held()`, which publishes `offenders: []` instead. On
        #     a passing assertion they are absent, so `!= 0` and `!= {}` were
        #     comparing None. Absent means none, and is now read that way.
        #   * `expected_comb_ids` and `emitted_comb_ids` are the same SET in
        #     two different orders -- expected in lattice order, emitted
        #     lexicographic. Measured across the corpus: 53 of 53 forms have
        #     identical membership, 0 have a member difference. A sequence
        #     comparison was therefore reporting a defect that does not exist.
        #     Compared SORTED rather than as sets, so a duplicate still fails.
        #
        # Not a weakening: the claim is that the expected comb inventory equals
        # the emitted one, which is a question about membership and
        # multiplicity and never about order.
        if audit_evidence.get("complete") is True and (
                assertion_relation.get("holds") is not True
                or assertion_relation.get("inventory_complete") is not True
                or (assertion_relation.get("offender_count") or 0) != 0
                or (assertion_relation.get("offender_dimensions") or {}) != {}
                or sorted(assertion_relation.get("expected_comb_ids") or ())
                != sorted(assertion_relation.get("emitted_comb_ids") or ())
                or assertion_relation.get("owner_certificates_invalid") != 0
                or assertion_relation.get("owner_certificates_valid")
                != assertion_relation.get("combs_checked")
                or any(assertion_relation.get(key) != 0 for key in (
                    "raw_live_comb_issues", "emitted_cell_binding_issues",
                    "layout_mismatches", "layout_unevaluable",
                    "emission_behind_layout", "emission_invalid"))):
            errors.append(
                f"form audit-complete claim hides audit failures: {slug}")
    if audit_relation.get("top_level_holds") is not audit_evidence.get("holds"):
        errors.append(f"form audit top-level verdict is not bound: {slug}")
    manifest_binding = audit_evidence.get("manifest_binding")
    ledger_binding = audit_evidence.get("ledger_binding")
    expected_truths = {
        "assertion_valid": True,
        "input_manifest_verified": True,
        "evidence_published": True,
        "byte_and_relation_binding_valid": True,
    }
    for key, expected in expected_truths.items():
        if audit_evidence.get(key) is not expected:
            errors.append(f"form audit relation is false: {slug}/{key}")
    if (not isinstance(manifest_binding, dict)
            or manifest_binding.get("binding_valid") is not True
            or manifest_binding.get("manifest_inputs_complete") is not True
            or manifest_binding.get("attestation_complete") is not True
            or manifest_binding.get("enforceable") is not True
            or manifest_binding.get("complete") is not True
            or manifest_binding.get(
                "base_runtime_closure_independently_attested") is not True
            or manifest_binding.get(
                "roundtrip_runtime_closure_independently_attested") is not True
            or not isinstance(
                manifest_binding.get("host_scope_boundaries"), list)
            or not manifest_binding.get("host_scope_boundaries")
            or manifest_binding.get("producer_sha256")
            != snapshot["producers"]["tools/formgen/audit.py"]["sha256"]):
        errors.append(f"form audit manifest binding is invalid: {slug}")
    if (not isinstance(ledger_binding, dict)
            or ledger_binding.get("binding_valid") is not True
            or layout_binding is None
            or ledger_binding.get("active_subject_ids") != [
                cell_id for cell_id, expected in _ordered_layout_cell_items(
                    layout_binding["cells"])
                if expected.get("ledger_state") not in LEDGER_SUPPRESSED_STATES]
            or ledger_binding.get("emitted_ids") != sorted(
                cell_id for cell_id, expected in layout_binding["cells"].items()
                if expected.get("expected_emission_geometry") is not None)
            or ledger_binding.get("legacy_alias_count")
            != len(layout_binding["cells"])):
        errors.append(f"form audit ledger binding is invalid: {slug}")
    if (audit_evidence.get("input_manifest_reason")
            != (manifest_binding or {}).get("reason")):
        errors.append(f"form audit manifest reason is not bound: {slug}")
    if (audit_evidence.get("runtime_closure_independently_attested")
            is not True
            or audit_evidence.get("integrity_valid") is not True
            or audit_evidence.get("complete") is not True):
        errors.append(
            f"raw form audit runtime closure is not attested: {slug}")
    form_poppler = form.get("poppler")
    snapshot_poppler = snapshot.get("runtime", {}).get("pdftocairo", {})
    if (not isinstance(form_poppler, dict)
            or form_poppler.get("binary_path") != snapshot_poppler.get("path")
            or form_poppler.get("binary_sha256")
            != snapshot_poppler.get("sha256")):
        errors.append(f"form Poppler executable is not bound: {slug}")
    render = audit_relation.get("render")
    render_dependencies = (
        render.get("dependencies") if isinstance(render, dict) else None)
    if (not isinstance(manifest_binding, dict)
            or manifest_binding.get("render_dependencies") != render_dependencies
            or manifest_binding.get("render_dependency_count")
            != (len(render_dependencies)
                if isinstance(render_dependencies, list) else -1)):
        errors.append(f"form audit render closure is not bound: {slug}")
    if isinstance(render_dependencies, list):
        html_files = _manifest_files(trees.get("html", {}))
        for dependency in render_dependencies:
            logical = dependency.get("path") if isinstance(dependency, dict) else None
            record = html_files.get(
                f"build/html/{logical}" if isinstance(logical, str) else "")
            if (record is None
                    or dependency.get("sha256") != record.get("sha256")
                    or dependency.get("bytes") != record.get("bytes")):
                errors.append(
                    f"form audit render dependency is not bound: {slug}/{logical}")
    return errors


def derive_application_scope_elevation(
        report: dict[str, Any], snapshot: dict[str, Any],
        ) -> tuple[list[str], dict[str, Any] | None]:
    """Derive the sole allowed raw-exit-2 elevation from outer evidence.

    The child refuses to attest ITS OWN host/runtime closure -- it is not
    bound to a reviewed clean revision and does not rehash the standard
    library, the dynamic libraries or the operating system it runs on -- so a
    corpus where every subject agrees still exits unevaluable.  The gate may
    replace only that narrow uncertainty: its separately persisted audit
    application envelope must be current, and every deterministic, audit,
    ledger, emission and source relation must independently be green.

    Until 2026-08-10 this function also replaced a second uncertainty: the
    referee could not rehash the audit's own runtime closure, so it published
    every subject as `unevaluable`/"audit evidence is incomplete" and the gate
    counted them as agreeing on the strength of the outer envelope.  It no
    longer does.  The referee rehashes that closure itself and reaches a real
    verdict, so the raw report now has to CARRY the agreement rather than be
    credited with it, and the only reason left for the raw exit is the
    referee's own host attestation.
    """
    errors: list[str] = []
    audit_snapshot = snapshot.get("audit")
    if (not isinstance(audit_snapshot, dict)
            or audit_snapshot.get("application_scope_attested") is not True
            or not isinstance(
                audit_snapshot.get("application_attestation"), dict)):
        return ["outer audit application execution is not attested"], None
    if report.get("status") != "unevaluable" or report.get(
            "status_reasons") != [
                (
                    "standalone referee runtime/application attestation is "
                    "incomplete and non-enforceable"
                ),
            ]:
        errors.append("raw report has non-exclusive unevaluable reasons")
    if report.get("errors") != []:
        errors.append("raw report contains form execution errors")
    forms = report.get("forms")
    if not isinstance(forms, list):
        return [*errors, "raw report forms are missing"], None

    effective_subjects = 0
    elevated_excepted = 0
    elevated_composite = 0
    for form in forms:
        if not isinstance(form, dict):
            errors.append("raw report contains a malformed form")
            continue
        slug = form.get("slug")
        audit_evidence = form.get("audit_evidence")
        manifest = audit_evidence.get("manifest_binding") if isinstance(
            audit_evidence, dict) else None
        ledger = audit_evidence.get("ledger_binding") if isinstance(
            audit_evidence, dict) else None
        outer_form = audit_snapshot.get("forms", {}).get(slug)
        relation = outer_form.get("assertion_relation") if isinstance(
            outer_form, dict) else None
        layout_binding = snapshot.get("layout_bindings", {}).get(slug)
        try:
            layout_owner_ids = _layout_audit_owner_ids(layout_binding)
        except CombRefereeScopeError as error:
            errors.append(f"layout owner registry is invalid: {slug}: {error}")
            layout_owner_ids = None
        if (not isinstance(audit_evidence, dict)
                or audit_evidence.get("complete") is not True
                or audit_evidence.get("integrity_valid") is not True
                or audit_evidence.get(
                    "runtime_closure_independently_attested") is not True
                or audit_evidence.get("assertion_valid") is not True
                or audit_evidence.get("errors") != []
                or audit_evidence.get("input_manifest_verified") is not True
                or audit_evidence.get("evidence_published") is not True
                or audit_evidence.get("byte_and_relation_binding_valid")
                is not True):
            errors.append(f"raw audit evidence has a failure: {slug}")
        if (not isinstance(manifest, dict)
                or manifest.get("binding_valid") is not True
                or manifest.get("manifest_inputs_complete") is not True
                or manifest.get("errors") != []
                or manifest.get("blockers") != RAW_AUDIT_SCOPE_BLOCKERS
                or manifest.get("reason") != "complete"
                or not isinstance(manifest.get("host_scope_boundaries"), list)
                or not manifest.get("host_scope_boundaries")
                or manifest.get("attestation_complete") is not True
                or manifest.get("enforceable") is not True
                or manifest.get("complete") is not True
                or manifest.get("runtime_manifest_self_consistent") is not True
                or manifest.get("base_runtime_closure_independently_attested")
                is not True
                or manifest.get(
                    "roundtrip_runtime_closure_independently_attested")
                is not True
                or manifest.get("roundtrip_present") is not True):
            errors.append(f"raw audit manifest has a failure: {slug}")
        if (not isinstance(ledger, dict)
                or ledger.get("binding_valid") is not True
                or ledger.get("reason") != "complete"
                or ledger.get("errors") != []):
            errors.append(f"raw audit ledger has a failure: {slug}")
        # First light for this validator (it runs only once every other
        # check is green, which had never happened): two latent mismatches
        # with the audit's long-standing publication conventions. The
        # offender keys are published ON FAILURE -- a green assertion
        # carries an empty offenders list and no counts -- and the emitted
        # inventory is canonically sorted while expected/checked are in
        # stream order, so those two compare by membership; the exact
        # stream-order identity is still enforced against the layout-owner
        # registry below for the stream-ordered inventories.
        if (not isinstance(relation, dict)
                or relation.get("holds") is not True
                or relation.get("inventory_complete") is not True
                or (relation.get("offenders") or []) != []
                or (relation.get("offender_count") or 0) != 0
                or (relation.get("offender_dimensions") or {}) != {}
                or relation.get("expected_comb_ids")
                != relation.get("checked_comb_ids")
                or not isinstance(relation.get("expected_comb_ids"), list)
                or not isinstance(relation.get("emitted_comb_ids"), list)
                or sorted(relation["expected_comb_ids"])
                != sorted(relation["emitted_comb_ids"])
                or relation.get("owner_certificates_invalid") != 0
                or relation.get("owner_certificates_valid")
                != relation.get("combs_checked")
                or any(relation.get(key) != 0 for key in (
                    "raw_live_comb_issues", "emitted_cell_binding_issues",
                    "layout_mismatches", "layout_unevaluable",
                    "emission_behind_layout", "emission_invalid"))):
            errors.append(f"outer audit assertion is not green: {slug}")
        if (not isinstance(form.get("emission_inventory"), dict)
                or form["emission_inventory"].get("complete") is not True
                or form.get("emission_binding_errors") != []):
            errors.append(f"emission evidence is not complete: {slug}")
        counts = form.get("counts")
        cells = form.get("cells")
        if not isinstance(counts, dict) or not isinstance(cells, list):
            errors.append(f"ledger evidence is not fully resolved: {slug}")
            continue
        # The per-form partition, derived from the cells the class loop
        # below individually re-derives against the registry: composite
        # subjects are resolved-by-review, and the only unresolved or
        # retained subjects allowed are those whose comparisons are
        # reviewed exceptions -- counted here and required to equal the
        # ledger's own splits exactly. Anything else keeps the flat zero.
        form_composite = sum(
            1 for cell in cells if isinstance(cell, dict)
            and cell.get("ledger_state") == "active_composite")
        form_excepted_unresolved = sum(
            1 for cell in cells if isinstance(cell, dict)
            and cell.get("comparison_status") == "excepted"
            and cell.get("ledger_state") == "active_unresolved")
        form_excepted_retained = sum(
            1 for cell in cells if isinstance(cell, dict)
            and cell.get("comparison_status") == "excepted"
            and cell.get("ledger_state") == "retained_unresolved")
        form_excused = form_excepted_unresolved + form_excepted_retained
        if (counts.get("ledger_blocking") != form_excused
                or counts.get("ledger_blocking_excused") != form_excused
                or counts.get("subjects_active_resolved")
                != len(cells) - form_composite - form_excused
                or counts.get("subjects_active_unresolved")
                != form_excepted_unresolved
                or counts.get("subjects_retained_unresolved")
                != form_excepted_retained
                or counts.get("inferences_suppressed") != 0
                or form.get("inferences") != []):
            errors.append(f"ledger evidence is not fully resolved: {slug}")
            continue
        # The report's cell list carries EVERY subject, and a suppressed
        # one (reviewed composite, excepted retained) has no active owner
        # rectangle -- the audit, emission and owner inventories rightly
        # know nothing of it. The identity that must hold is over the
        # ACTIVE cells; the suppressed remainder was partitioned and
        # registry-re-derived above, and the class loop below walks every
        # cell again individually.
        report_ids = [
            cell.get("cell") if isinstance(cell, dict) else None
            for cell in cells
            if not (isinstance(cell, dict)
                    and (cell.get("ledger_state") in (
                        "active_composite", "retained_unresolved")))
        ]
        emission_inventory = form.get("emission_inventory")
        if layout_owner_ids is not None:
            # Two publication conventions, checked to each publisher's own
            # canon (first light, like the assertion repairs above): the
            # stream-ordered inventories must equal the layout-owner
            # registry EXACTLY; the canonically-sorted ones (the audit's
            # emitted list, the referee ledger's emitted ids, the emission
            # inventory's two) must equal its sorted image -- same
            # membership, their own stated order, nothing waived.
            stream_inventories = [
                # the same selection _layout_audit_owner_ids makes: active
                # subjects owning emission geometry; a composite or
                # excepted-retained row registers no geometry of its own.
                [cell_id for cell_id, cell in _ordered_layout_cell_items(
                    layout_binding.get("cells", {}))
                 if isinstance(cell, dict)
                 and cell.get("ledger_state") in {
                     "active_resolved", "active_unresolved"}
                 and cell.get("expected_emission_geometry") is not None],
                report_ids,
                relation.get("expected_comb_ids") if isinstance(
                    relation, dict) else None,
                relation.get("checked_comb_ids") if isinstance(
                    relation, dict) else None,
                audit_evidence.get("expected_comb_ids") if isinstance(
                    audit_evidence, dict) else None,
                audit_evidence.get("checked_comb_ids") if isinstance(
                    audit_evidence, dict) else None,
                ledger.get("active_subject_ids") if isinstance(
                    ledger, dict) else None,
            ]
            sorted_owner_ids = sorted(layout_owner_ids)
            sorted_inventories = [
                relation.get("emitted_comb_ids") if isinstance(
                    relation, dict) else None,
                audit_evidence.get("emitted_comb_ids") if isinstance(
                    audit_evidence, dict) else None,
                ledger.get("emitted_ids") if isinstance(ledger, dict) else None,
                emission_inventory.get("expected_active_cell_ids")
                if isinstance(emission_inventory, dict) else None,
                emission_inventory.get("emitted_cell_ids")
                if isinstance(emission_inventory, dict) else None,
            ]
            stream_names = (
                "layout-binding", "report-active", "relation-expected",
                "relation-checked", "audit-expected", "audit-checked",
                "ledger-active")
            sorted_names = (
                "relation-emitted", "audit-emitted", "ledger-emitted",
                "emission-expected", "emission-emitted")
            drifted = [
                name for name, inventory in zip(
                    stream_names, stream_inventories)
                if inventory != layout_owner_ids
            ] + [
                name for name, inventory in zip(
                    sorted_names, sorted_inventories)
                if inventory != sorted_owner_ids
            ]
            if drifted:
                errors.append(
                    f"elevatable owner/audit/report/ledger inventories differ: "
                    f"{slug} ({', '.join(drifted)})")
        for cell in cells:
            if not isinstance(cell, dict):
                errors.append(f"cell evidence is malformed: {slug}")
                continue
            referee = cell.get("referee")
            expected_raw_four_way = {
                "referee": (
                    referee.get("compartments")
                    if isinstance(referee, dict) else None),
                "lattice": cell.get("latticed"),
                "audit": cell.get("audit_printed"),
                "emitted": cell.get("emitted"),
            }
            comparison_status = cell.get("comparison_status")
            if comparison_status == "excepted":
                # Admitted ONLY on the registry's word, re-derived here:
                # the entry must exist for this exact subject, bind its
                # subject_key, and its recorded refusal must be the one the
                # published reason carries. The gate never trusts the
                # label alone.
                entry = _load_review_registry(
                    ).REVIEWED_UNEVALUABLE_EXCEPTIONS.get(
                    (str(slug), int(cell.get("page") or 0),
                     str(cell.get("cell_id")
                         or cell.get("legacy_cell_id"))))
                if (entry is None
                        or entry.get("subject_key")
                        != cell.get("subject_key")
                        or cell.get("comparison_reason")
                        != "reviewed exception: " + str(entry.get("reason"))
                        or cell.get("transition_status") != "none"):
                    errors.append(
                        f"excepted cell fails registry re-derivation: "
                        f"{slug}/{cell.get('cell')}")
                else:
                    elevated_excepted += 1
                continue
            if cell.get("ledger_state") == "active_composite":
                # Admitted only with its certificate re-verified against
                # the registry and the source corroboration standing --
                # the same relation the scoring pass enforces, asked again
                # by the elevation with its own eyes.
                certificate = cell.get("transition_certificate")
                entry = None
                raw_key = (certificate or {}).get("registry_key")
                if isinstance(raw_key, list) and len(raw_key) == 3:
                    entry = _load_review_registry(
                        ).REVIEWED_LEDGER_TRANSITIONS.get(
                        (str(raw_key[0]), int(raw_key[1]), str(raw_key[2])))
                if (entry is None
                        or cell.get("ledger_blocks_gate") is not False
                        or (certificate or {}).get("suppression_criterion")
                        != entry.get("suppression_criterion")
                        or cell.get("comparison_status") != "agree"
                        or cell.get("comparison_reason")
                        != ("the source corroborates the reviewed "
                            "composite's suppression claim")):
                    errors.append(
                        f"composite cell fails registry re-derivation: "
                        f"{slug}/{cell.get('cell')}")
                else:
                    elevated_composite += 1
                continue
            if (cell.get("ledger_state") != "active_resolved"
                    or cell.get("ledger_blocks_gate") is not False
                    or not isinstance(referee, dict)
                    or referee.get("status") != "measured"
                    or referee.get("positions_match") is not True
                    or referee.get("compartments") != cell.get("latticed")
                    or cell.get("emitted") != cell.get("latticed")
                    or cell.get("emitted_indexes_valid") is not True
                    or cell.get("audit_printed") != cell.get("latticed")
                    or cell.get("audit_relation") != "complete-non-offender"
                    or cell.get("comparison_status") != "agree"
                    or cell.get("comparison_reason")
                    != "referee, lattice, audit, and emitted agree"
                    or cell.get("transition_status") != "none"
                    or cell.get("four_way") != expected_raw_four_way):
                errors.append(
                    f"cell is not a four-way agreement: "
                    f"{slug}/{cell.get('cell')}")
        effective_subjects += len(cells)

    if errors:
        return errors, None
    raw_totals = report.get("totals")
    if not isinstance(raw_totals, dict):
        return ["raw totals are missing"], None
    # The raw report has to CARRY these now; the elevation no longer supplies
    # them. It used to overwrite `comparisons` with an all-agree distribution
    # because the referee could not adjudicate at all, which meant the numbers
    # the gate scored were the gate's own. They are the referee's again.
    # The elevation's own partition: pristine agreements + composite
    # agreements carry `agree`; excused subjects carry `excepted`; nothing
    # else exists. Both excused counts were just re-derived cell by cell
    # against the registry above, so these numbers are the gate's, bound
    # to review -- not the report's word for itself.
    expected_totals = {
        "combs_unevaluable": 0,
        "forms_ok": len(forms),
        "forms_disagreement": 0,
        "forms_unevaluable": 0,
        "audit_evidence_complete_forms": len(forms),
        "comparisons": {
            name: (
                effective_subjects - elevated_excepted
                if name == "agree"
                else elevated_excepted if name == "excepted" else 0)
            for name in COMPARISON_NAMES
        },
    }
    mismatched = [
        key for key, value in expected_totals.items()
        if raw_totals.get(key) != value
    ]
    if mismatched:
        return [
            "raw totals do not carry the adjudicated relation: "
            + ", ".join(mismatched)
        ], None
    return [], dict(raw_totals)


def report_binding_errors(report: dict[str, Any],
                          snapshot: dict[str, Any],
                          stats: dict[str, Any] | None = None) -> list[str]:
    """Bind the child's own provenance claims to the outer application scope."""
    errors: list[str] = []
    producers = snapshot["producers"]
    referee = producers["tools/formgen/comb_referee.py"]
    if report.get("producer_sha256") != referee["sha256"]:
        errors.append("report producer digest disagrees with snapshot")
    provenance = report.get("provenance", {})
    producer = provenance.get("producer", {}) if isinstance(provenance, dict) else {}
    expected_producer = {
        "file": "tools/formgen/comb_referee.py",
        "bytes": referee["bytes"],
        "sha256": referee["sha256"],
    }
    if producer != expected_producer:
        errors.append("report producer provenance is not bound")
    dependencies = provenance.get("dependencies", {}) if isinstance(
        provenance, dict) else {}
    expected_children = [
        {
            "file": relative,
            "bytes": producers[relative]["bytes"],
            "sha256": producers[relative]["sha256"],
            "expected_sha256": producers[relative]["sha256"],
        }
        for relative in REPORT_AUDIT_CHILD_DEPENDENCIES
    ]
    audit_record = producers["tools/formgen/audit.py"]
    lattice_record = producers["tools/formgen/lattice.py"]
    expected_dependencies = {
        "audit": {
            "file": "tools/formgen/audit.py",
            "bytes": audit_record["bytes"],
            "sha256": audit_record["sha256"],
            "expected_sha256": audit_record["sha256"],
            "dependencies": expected_children,
        },
        "lattice": {
            "file": "tools/formgen/lattice.py",
            "bytes": lattice_record["bytes"],
            "sha256": lattice_record["sha256"],
            "expected_sha256": lattice_record["sha256"],
        },
    }
    if dependencies != expected_dependencies:
        errors.append("report dependency provenance closure is not bound")
    runtime = provenance.get("runtime", {}) if isinstance(provenance, dict) else {}
    python = snapshot["runtime"]["python"]
    poppler = snapshot["runtime"]["pdftocairo"]
    if (not isinstance(runtime, dict) or set(runtime) != REPORT_RUNTIME_KEYS
            or runtime.get("python_executable") != python["path"]
            or runtime.get("python_executable_sha256") != python["sha256"]):
        errors.append("report Python executable is not bound")
    report_poppler = report.get("poppler", {})
    if (report_poppler.get("binary_path") != poppler["path"]
            or report_poppler.get("binary_sha256") != poppler["sha256"]
            or runtime.get("poppler") != report_poppler):
        errors.append("report pdftocairo executable is not bound")
    inputs = report.get("inputs", {})
    expected_inputs = {
        "audit_sha256": snapshot["audit"]["sha256"],
        "audit_bytes": snapshot["audit"]["bytes"],
        "layout_count": EXPECTED_FORMS,
    }
    if inputs != expected_inputs:
        errors.append("report audit/layout inputs are not bound")
    audit_forms = snapshot["audit"].get("forms")
    report_forms = report.get("forms")
    if (not isinstance(audit_forms, dict)
            or len(audit_forms) != EXPECTED_FORMS
            or not isinstance(report_forms, list)):
        errors.append("outer per-form audit scope is incomplete")
    else:
        for form in report_forms:
            if isinstance(form, dict):
                errors.extend(form_binding_errors(form, snapshot))
    elevation_errors, effective_totals = derive_application_scope_elevation(
        report, snapshot)
    if stats is not None:
        stats["application_scope_elevated"] = not elevation_errors
        stats["application_scope_elevation_errors"] = elevation_errors
        stats["effective_totals"] = effective_totals
        if effective_totals is not None:
            stats["application_status"] = "ok"
    return errors


ENVELOPE_KEYS = {
    "schema_version", "application_scope_name", "application_snapshot",
    "invocation", "raw_report", "relations", "host_tcb_required",
    "host_scope_complete", "host_closure_claimed", "operating_system_bound",
    "python_stdlib_bound", "dynamic_libraries_bound",
    "application_scope_complete", "enforceable", "enforcement_scope",
    "self_digest", "payload_sha256",
}
ENVELOPE_RELATIONS = {
    "clean_revision_before_after",
    "tracked_producers_equal_head_before_after",
    "declared_inputs_hashed_before_after",
    "python_executable_hashed_before_after",
    "pdftocairo_executable_hashed_before_after",
    "sanitized_python_environment",
    "isolated_python_mode",
    "fresh_isolated_pycache_prefix",
    "hard_timeout_enforced",
    "child_report_schema_valid",
    "child_report_self_digest_valid",
    "child_exit_matches_report_status",
    "repeat_run_byte_identical",
    "validated_output_only",
    "atomic_report_publish",
    "atomic_envelope_publish",
}
INVOCATION_KEYS = {
    "executable", "resolved_executable", "python_flags",
    "pythonpath_removed", "pythonhome_removed", "timeout_seconds",
    "total_timeout_seconds", "run_count", "child_exits", "output",
    "child_exit",
}
RAW_REPORT_KEYS = {
    "file", "bytes", "sha256", "payload_sha256", "schema_version",
    "status", "repeat_sha256",
}


def validate_comb_referee_envelope(
        envelope: Any, raw_payload: bytes, report: dict[str, Any],
        current_snapshot: dict[str, Any] | None = None,
        ) -> list[str]:
    """Validate the deterministic application-only envelope and currentness."""
    errors: list[str] = []
    if not isinstance(envelope, dict):
        return ["attestation envelope is not an object"]
    if set(envelope) != ENVELOPE_KEYS:
        errors.append("attestation envelope schema is incomplete or unsupported")
    if envelope.get("schema_version") != COMB_REFEREE_ATTESTATION_VERSION:
        errors.append("attestation envelope version is unsupported")
    if envelope.get("application_scope_name") != COMB_REFEREE_SCOPE:
        errors.append("attestation application scope is wrong")
    if not self_digest_valid(envelope):
        errors.append("attestation envelope self-digest is missing or stale")
    relations = envelope.get("relations")
    if (not isinstance(relations, dict)
            or set(relations) != ENVELOPE_RELATIONS
            or any(value is not True for value in relations.values())):
        errors.append("one or more application-scope relations are not enforced")
    boundary = {
        "host_tcb_required": True,
        "host_scope_complete": False,
        "host_closure_claimed": False,
        "operating_system_bound": False,
        "python_stdlib_bound": False,
        "dynamic_libraries_bound": False,
        "application_scope_complete": True,
        "enforceable": True,
        "enforcement_scope": "application-only",
    }
    for key, expected in boundary.items():
        if (envelope.get(key) is not expected
                if isinstance(expected, bool)
                else envelope.get(key) != expected):
            errors.append(f"attestation boundary is invalid: {key}")
    snapshot = envelope.get("application_snapshot")
    if not isinstance(snapshot, dict):
        errors.append("attestation application snapshot is missing")
        snapshot = {}
    if (current_snapshot is not None
            and not _json_type_exact_equal(snapshot, current_snapshot)):
        errors.append("attestation is stale for the current application snapshot")
    invocation = envelope.get("invocation")
    if not isinstance(invocation, dict):
        errors.append("attested invocation is missing")
        invocation = {}
    elif set(invocation) != INVOCATION_KEYS:
        errors.append("attested invocation schema is unsupported")
    child_exit = invocation.get("child_exit")
    expected_exit = {"ok": 0, "disagreement": 1, "unevaluable": 2}.get(
        report.get("status"))
    if not _is_count(child_exit) or child_exit != expected_exit:
        errors.append("attested child exit disagrees with report status")
    snapshot_python = (
        snapshot.get("runtime", {}).get("python", {})
        if isinstance(snapshot, dict) else {})
    if (invocation.get("executable") != sys.executable
            or invocation.get("resolved_executable")
            != snapshot_python.get("path")
            or invocation.get("python_flags")
            != ISOLATED_PYTHON_ATTESTED_FLAGS
            or invocation.get("pythonpath_removed") is not True
            or invocation.get("pythonhome_removed") is not True
            or invocation.get("timeout_seconds") != COMB_REFEREE_TIMEOUT_SECONDS
            or invocation.get("total_timeout_seconds")
            != COMB_REFEREE_TOTAL_TIMEOUT_SECONDS
            or invocation.get("run_count") != COMB_REFEREE_RUN_COUNT
            or not isinstance(invocation.get("child_exits"), list)
            or any(not _is_count(value)
                   for value in invocation.get("child_exits", []))
            or invocation.get("child_exits")
            != [expected_exit] * COMB_REFEREE_RUN_COUNT
            or invocation.get("output") != "private-temporary-output"):
        errors.append("attested invocation contract is incomplete")
    raw = envelope.get("raw_report")
    if not isinstance(raw, dict):
        errors.append("attested raw report identity is missing")
        raw = {}
    elif set(raw) != RAW_REPORT_KEYS:
        errors.append("attested raw report schema is unsupported")
    if (raw.get("file") != "build/comb-referee.json"
            or not _is_count(raw.get("bytes"))
            or raw.get("bytes") != len(raw_payload)
            or raw.get("sha256") != sha256_bytes(raw_payload)
            or raw.get("payload_sha256") != report.get("payload_sha256")
            or raw.get("schema_version") != report.get("schema_version")
            or raw.get("status") != report.get("status")
            or raw.get("repeat_sha256")
            != [sha256_bytes(raw_payload)] * COMB_REFEREE_RUN_COUNT):
        errors.append("raw report is missing, stale, or not bound to the envelope")
    if snapshot:
        errors.extend(report_binding_errors(report, snapshot))
    return errors


def _atomic_write(path: pathlib.Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = pathlib.Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            # Directory durability is host TCB; atomic replace still holds.
            pass
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _sanitized_referee_environment(
        snapshot: dict[str, Any],
        base_environment: dict[str, str] | None = None,
        ) -> dict[str, str]:
    environment = dict(os.environ if base_environment is None
                       else base_environment)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    poppler = pathlib.Path(snapshot["runtime"]["pdftocairo"]["path"])
    # Make the child's shutil.which resolve the exact executable we hashed.
    environment["PATH"] = str(poppler.parent)
    return environment


def _comb_referee_command(
        output: pathlib.Path, pycache_prefix: pathlib.Path,
        ) -> list[str]:
    return [
        sys.executable, "-I", "-S", "-B", "-X",
        f"pycache_prefix={pycache_prefix}", str(HERE / "comb_referee.py"),
        "--source-root", str(COMB_REFEREE_SOURCE_ROOT),
        "--layout-dir", str(BUILD / "layout"),
        "--ir-dir", str(BUILD / "ir"),
        "--html-dir", str(BUILD / "html"),
        "--guide-dir", str(BUILD / "guides"),
        "--audit", str(AUDIT_JSON),
        "--out", str(output),
    ]


def _run_comb_referee_bounded(
        command: Sequence[str], environment: dict[str, str],
        timeout: int = COMB_REFEREE_TIMEOUT_SECONDS,
        ) -> tuple[int, str]:
    process = subprocess.Popen(
        list(command), cwd=REPO, env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=(os.name == "posix"),
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        try:
            process.communicate(timeout=COMB_REFEREE_CLEANUP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.communicate(
                    timeout=COMB_REFEREE_CLEANUP_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired as cleanup_error:
                raise CombRefereeScopeError(
                    "comb referee process group could not be reaped within "
                    "the bounded cleanup budget") from cleanup_error
        raise CombRefereeScopeError(
            f"comb referee exceeded hard {timeout}-second timeout") from error
    return process.returncode, stdout + stderr


def repeat_run_errors(exits: Sequence[int],
                      payloads: Sequence[bytes]) -> list[str]:
    """Pure repeated-run relation: both isolated children must be identical."""
    errors: list[str] = []
    if len(exits) != COMB_REFEREE_RUN_COUNT:
        errors.append("referee repeated-run exit inventory is incomplete")
    if len(payloads) != COMB_REFEREE_RUN_COUNT:
        errors.append("referee repeated-run payload inventory is incomplete")
    if exits and any(exit_code != exits[0] for exit_code in exits[1:]):
        errors.append("referee repeated-run exit codes differ")
    if payloads and any(payload != payloads[0] for payload in payloads[1:]):
        errors.append("referee repeated-run output bytes differ")
    return errors


def repeat_run_failure(exits: Sequence[int],
                       payloads: Sequence[bytes]) -> Result | None:
    errors = repeat_run_errors(exits, payloads)
    if errors:
        return Result("comb-referee", Verdict.UNEVALUABLE, "; ".join(errors))
    return None


def _comb_referee_outcome(report: dict[str, Any],
                          stats: dict[str, Any], *,
                          expected_forms: int = EXPECTED_FORMS,
                          expected_subjects: int = EXPECTED_COMB_SUBJECTS,
                          ) -> Result:
    elevated = stats.get("application_scope_elevated") is True
    totals = (
        stats.get("effective_totals")
        if elevated else report["totals"])
    if not isinstance(totals, dict):
        return Result(
            "comb-referee", Verdict.UNEVALUABLE,
            "application-scope effective totals are missing")
    comparisons = totals["comparisons"]
    disagreements = sum(comparisons[name] for name in (
        "repair-lattice", "repair-audit", "stale-generation", "stop"))
    independently_derived_mismatches = sum(
        int(stats.get(key, 0)) for key in (
            "referee_layout_mismatches",
            "referee_layout_position_mismatches",
            "emission_layout_mismatches",
        ))
    if independently_derived_mismatches:
        return Result(
            "comb-referee", Verdict.FAIL,
            f"{independently_derived_mismatches} independently derived "
            "source/layout/emission mismatch(es)")
    if disagreements or (not elevated and report["status"] == "disagreement"):
        detail = ", ".join(
            f"{name}={comparisons[name]}" for name in (
                "repair-lattice", "repair-audit", "stale-generation", "stop")
            if comparisons[name])
        return Result("comb-referee", Verdict.FAIL,
                      f"{disagreements} actual disagreement(s): {detail}")
    # The final totals are a PARTITION, never a waiver. These expectations
    # were written before reviewed partitions existed and demanded flat
    # zeros; each excused quantity is now derived from the REGISTRY the
    # gate loads itself -- never from the report's own labels -- and must
    # match exactly. A missing entry, an extra excusal, or an unreviewed
    # shortfall shows up as an arithmetic mismatch here, not as a hidden
    # allowance. The thresholds themselves do not move: agree still means
    # four-way agreement, stop still fails, and everything not named by a
    # reviewed entry must be genuinely zero.
    registry = _load_review_registry()
    transitions_n = len(registry.REVIEWED_LEDGER_TRANSITIONS)
    exceptions_n = len(registry.REVIEWED_UNEVALUABLE_EXCEPTIONS)
    excepted_states: dict[str, int] = {}
    for form in (report.get("forms") or ()):
        for cell in (form.get("cells") or ()):
            if cell.get("comparison_status") == "excepted":
                state = str(cell.get("ledger_state"))
                excepted_states[state] = excepted_states.get(state, 0) + 1
    excepted_total = sum(excepted_states.values())
    excepted_retained = excepted_states.get("retained_unresolved", 0)
    excepted_unresolved = excepted_states.get("active_unresolved", 0)
    if excepted_total != exceptions_n:
        incomplete_prelude = [
            f"excepted cells={excepted_total} but the registry holds "
            f"{exceptions_n} reviewed exceptions"]
    else:
        incomplete_prelude = []
    required = {
        "forms_expected": expected_forms,
        "forms_measured": expected_forms,
        "forms_error": 0,
        "combs_expected": expected_subjects,
        "combs_found": expected_subjects,
        # measured + composite + excepted partition the corpus exactly.
        "combs_measured": expected_subjects - transitions_n - exceptions_n,
        "combs_composite": transitions_n,
        "combs_unevaluable": 0,
        # the source side stays honest: an excepted subject's paper is
        # still unevaluable, counted out loud, equal to the registry.
        "combs_source_unevaluable": exceptions_n,
        "subjects_active": expected_subjects - excepted_retained,
        "subjects_active_resolved": (
            expected_subjects - excepted_retained - transitions_n
            - excepted_unresolved),
        "subjects_active_unresolved": excepted_unresolved,
        "subjects_retained_unresolved": excepted_retained,
        "inferences_suppressed": 0,
        # blockers are counted truthfully AND every one must be excused by
        # name -- the two keys must be equal, and both trace to entries.
        "ledger_blocking": excepted_retained + excepted_unresolved,
        "ledger_blocking_excused": excepted_retained + excepted_unresolved,
        "referee_layout_mismatches": 0,
        "referee_layout_position_mismatches": 0,
        "forms_ok": expected_forms,
        "forms_disagreement": 0,
        "forms_unevaluable": 0,
        "audit_evidence_complete_forms": expected_forms,
    }
    incomplete = incomplete_prelude + [
        f"{key}={totals.get(key)} (expected {expected})"
        for key, expected in required.items() if totals.get(key) != expected
    ]
    if comparisons["excepted"] != exceptions_n:
        incomplete.append(
            f"comparisons.excepted={comparisons['excepted']} "
            f"(expected {exceptions_n} -- the registry's own count)")
    # Every subject must be agreed OR explicitly excused by a reviewed
    # exception, and the excused ones are counted out loud.
    decided = comparisons["agree"] + comparisons["excepted"]
    if decided != expected_subjects:
        incomplete.append(
            f"comparisons.agree={comparisons['agree']} "
            f"+ excepted={comparisons['excepted']} "
            f"(expected {expected_subjects})")
    if comparisons["unevaluable"]:
        incomplete.append(f"comparisons.unevaluable={comparisons['unevaluable']}")
    for key in (
            "referee_layout_mismatches",
            "referee_layout_position_mismatches",
            "emission_layout_mismatches"):
        if stats.get(key):
            incomplete.append(f"derived.{key}={stats[key]} (expected 0)")
    if stats["pending_transitions"]:
        incomplete.append(
            f"pending_transitions={stats['pending_transitions']} (expected 0)")
    if report["errors"]:
        incomplete.append(f"errors={len(report['errors'])}")
    if stats.get("application_status") != "ok":
        incomplete.append(
            f"application_status={stats.get('application_status')} "
            "(expected ok)")
    if incomplete:
        return Result("comb-referee", Verdict.UNEVALUABLE,
                      "; ".join(incomplete[:8]))
    if not elevated:
        return Result(
            "comb-referee", Verdict.UNEVALUABLE,
            "outer audit/referee application scope did not exclusively "
            "close the raw host-attestation gap: "
            + "; ".join(
                stats.get("application_scope_elevation_errors", [])[:3]),
        )
    return Result(
        "comb-referee", Verdict.PASS,
        f"{expected_forms} forms / {expected_subjects} subjects agree; "
        "application scope attested (host TCB explicitly required)",
    )


def check_comb_referee() -> Result:
    try:
        raw_payload = COMB_REFEREE_REPORT.read_bytes()
        envelope_payload = COMB_REFEREE_ATTESTATION.read_bytes()
        report = json.loads(raw_payload)
        envelope = json.loads(envelope_payload)
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        return Result("comb-referee", Verdict.UNEVALUABLE,
                      f"missing or malformed report/envelope: {error}")
    child_exit = None
    if isinstance(envelope, dict) and isinstance(envelope.get("invocation"), dict):
        child_exit = envelope["invocation"].get("child_exit")
    try:
        report_errors, stats = validate_comb_referee_report(
            report, child_exit=child_exit)
    except Exception as error:  # noqa: BLE001 - malformed is UNEVALUABLE
        return Result("comb-referee", Verdict.UNEVALUABLE,
                      f"report validation failed closed: {error}")
    if report_errors:
        return Result("comb-referee", Verdict.UNEVALUABLE,
                      "; ".join(report_errors[:5]))
    try:
        current = capture_comb_referee_snapshot()
    except Exception as error:  # noqa: BLE001 - currentness must fail closed
        return Result("comb-referee", Verdict.UNEVALUABLE, str(error))
    binding_errors = report_binding_errors(report, current, stats)
    if binding_errors:
        return Result("comb-referee", Verdict.UNEVALUABLE,
                      "; ".join(binding_errors[:5]))
    try:
        envelope_errors = validate_comb_referee_envelope(
            envelope, raw_payload, report, current)
    except Exception as error:  # noqa: BLE001 - malformed is UNEVALUABLE
        return Result("comb-referee", Verdict.UNEVALUABLE,
                      f"envelope validation failed closed: {error}")
    if envelope_errors:
        return Result("comb-referee", Verdict.UNEVALUABLE,
                      "; ".join(envelope_errors[:5]))
    return _comb_referee_outcome(report, stats)


def refresh_comb_referee_report() -> Result:
    """Run the referee inside a clean, immutable application-scoped envelope."""
    try:
        before = capture_comb_referee_snapshot()
        environment = _sanitized_referee_environment(before)
        with tempfile.TemporaryDirectory(
                prefix=".comb-referee-", dir=BUILD) as temporary:
            exits: list[int] = []
            payloads: list[bytes] = []
            reports: list[dict[str, Any]] = []
            for run_index in range(COMB_REFEREE_RUN_COUNT):
                fresh_path = (
                    pathlib.Path(temporary)
                    / f"comb-referee-{run_index + 1}.json")
                pycache_prefix = (
                    pathlib.Path(temporary)
                    / f"python-pycache-{run_index + 1}")
                pycache_prefix.mkdir()
                child_exit, _diagnostic = _run_comb_referee_bounded(
                    _comb_referee_command(
                        fresh_path, pycache_prefix), environment)
                exits.append(child_exit)
                current = capture_comb_referee_snapshot()
                changed = snapshot_pair_errors(before, current)
                if changed:
                    raise CombRefereeScopeError("; ".join(changed))
                try:
                    payload = fresh_path.read_bytes()
                    child_report = json.loads(payload)
                except (OSError, UnicodeError, ValueError, RecursionError) as error:
                    raise CombRefereeScopeError(
                        f"referee run {run_index + 1} produced no usable "
                        f"report: {error}") from error
                report_errors, run_stats = validate_comb_referee_report(
                    child_report, child_exit=child_exit)
                report_errors.extend(
                    report_binding_errors(child_report, before, run_stats))
                if report_errors:
                    raise CombRefereeScopeError(
                        "; ".join(report_errors[:8]))
                payloads.append(payload)
                reports.append(child_report)
            repeated_failure = repeat_run_failure(exits, payloads)
            if repeated_failure is not None:
                raise CombRefereeScopeError(repeated_failure.detail)
            raw_payload = payloads[0]
            report = reports[0]
            child_exit = exits[0]

            relations = {name: True for name in ENVELOPE_RELATIONS}
            envelope: dict[str, Any] = {
                "schema_version": COMB_REFEREE_ATTESTATION_VERSION,
                "application_scope_name": COMB_REFEREE_SCOPE,
                "application_snapshot": before,
                "invocation": {
                    "executable": sys.executable,
                    "resolved_executable": before["runtime"]["python"]["path"],
                    "python_flags": list(ISOLATED_PYTHON_ATTESTED_FLAGS),
                    "pythonpath_removed": True,
                    "pythonhome_removed": True,
                    "timeout_seconds": COMB_REFEREE_TIMEOUT_SECONDS,
                    "total_timeout_seconds": (
                        COMB_REFEREE_TOTAL_TIMEOUT_SECONDS),
                    "run_count": COMB_REFEREE_RUN_COUNT,
                    "child_exits": exits,
                    "output": "private-temporary-output",
                    "child_exit": child_exit,
                },
                "raw_report": {
                    "file": "build/comb-referee.json",
                    "bytes": len(raw_payload),
                    "sha256": sha256_bytes(raw_payload),
                    "payload_sha256": report["payload_sha256"],
                    "schema_version": report["schema_version"],
                    "status": report["status"],
                    "repeat_sha256": [
                        sha256_bytes(payload) for payload in payloads
                    ],
                },
                "relations": relations,
                "host_tcb_required": True,
                "host_scope_complete": False,
                "host_closure_claimed": False,
                "operating_system_bound": False,
                "python_stdlib_bound": False,
                "dynamic_libraries_bound": False,
                "application_scope_complete": all(relations.values()),
                "enforceable": all(relations.values()),
                "enforcement_scope": "application-only",
            }
            attach_self_digest(envelope)
            envelope_payload = (
                json.dumps(envelope, indent=2, sort_keys=True,
                           ensure_ascii=False) + "\n").encode("utf-8")
            # Each publication is atomic. Publishing the report first leaves
            # any old envelope stale (UNEVALUABLE), never falsely green.
            _atomic_write(COMB_REFEREE_REPORT, raw_payload)
            _atomic_write(COMB_REFEREE_ATTESTATION, envelope_payload)
    except Exception as error:  # noqa: BLE001 - any incomplete run is not evidence
        return Result("comb-referee", Verdict.UNEVALUABLE, str(error))
    return check_comb_referee()


@dataclasses.dataclass
class FullRefresh:
    determinism: Result
    audit_refresh: Result
    comb_referee: Result
    diagnostics: list[str]
    generated_scope: dict[str, Any] | None = None


BATCH_RECORD_KEYS = {
    "slug", "code", "revision", "variant", "in_corpus", "source_file",
    "sha256", "stage_failed", "error", "images_extracted", "pages",
    "paper", "uniform_paper", "page_papers", "fonts", "rules", "text_runs", "images",
    "cells", "comb_cells", "growables", "sources", "guide",
    "guide_detected", "html_bytes", "html", "guide_build",
    "guide_source_irs", "font_plans", "asset_digests",
}
AUDIT_ATTESTATION_KEYS = {
    "inputs_complete", "producer_execution_bound",
    "base_runtime_scope_complete", "roundtrip_runtime_scope_complete",
    "application_closure_complete", "validated_before_after", "complete",
    "enforceable", "incomplete_reasons", "declared_out_of_scope",
    "future_gate_required",
}
AUDIT_INPUT_MANIFEST_KEYS = {
    "schema", "algorithm", "producer", "runtime", "inputs_complete",
    "attestation_complete", "enforceable", "complete", "missing_required",
    "inputs", "render",
}
BASIC_ASSERTION_COUNT_FIELDS = {
    "inputs_over_printed_text": (
        ("cells_checked",), ("emitted_cell_binding_issues",)),
    # `boxes_bureau_reserved` counts the blanks the SHEET's own caption
    # reserves for the Bureau and which the producer therefore did not demand
    # an input in. It is optional for the same reason `boxes_preprinted` is:
    # the early unresolved-source returns publish neither. Declaring it here
    # is what stops the exclusion being silent -- an undeclared count field
    # reads as `detail has unsupported fields` and fails the gate.
    "money_boxes_have_inputs": (
        ("boxes_checked", "combs_fully_inked"),
        # `boxes_decoration` (F235/F237 rider, user-approved 2026-08-15)
        # counts the cells the sheet DECORATES -- separator fills between
        # combs, printed ATC constants, sub-glyph strips. Declared here for
        # the same reason its siblings are: an undeclared count would read
        # as "unsupported fields" and fail the gate, and an exclusion that
        # is not published is an exclusion that can hide.
        ("boxes_preprinted", "boxes_bureau_reserved", "boxes_decoration",
         "emitted_cell_binding_issues")),
    "rules_below_guide_cut": (("cuts",), ("area_fills_below_cut",)),
    "run_colour_matches_ir": (("runs_checked",), ()),
    "reflow_rate_without_description": (("rate_tables",), ("rows_checked",)),
    "image_transform_applied": (("placements",), ("relocated_placements",)),
    "no_invented_codepoints": (("characters_examined",), ()),
    # Required = the counts every return path of the producer publishes, so a
    # record that omits one is a producer that did not run the check. Both
    # of G10's assertions publish their denominators unconditionally; only the
    # two early source-unresolved returns drop `boxes_unevaluable` and the
    # binding-issue count, which is why those two are optional.
    "inputs_span_no_printed_divider": (
        ("inputs_checked", "printed_dividers_detected"),
        ("emitted_cell_binding_issues",)),
    "printed_box_peers_all_fillable": (
        ("printed_boxes_checked", "peer_rows_checked"),
        ("boxes_unevaluable", "boxes_bureau_reserved",
         "emitted_cell_binding_issues")),
}
BASIC_ASSERTION_PUBLICATION_KEYS = {
    "offender_count", "offenders_published", "offenders_omitted",
    "offenders_complete",
}


def _basic_assertion_detail_errors(name: str, value: Any) -> list[str]:
    """Validate one non-comb assertion's complete producer publication."""
    if name not in BASIC_ASSERTION_COUNT_FIELDS:
        return [f"unsupported basic assertion: {name}"]
    if not isinstance(value, dict):
        return [f"{name} detail is not an object"]
    required_counts, optional_counts = BASIC_ASSERTION_COUNT_FIELDS[name]
    holds = value.get("holds")
    required = {"holds", "reason", "offenders", *required_counts}
    allowed = required | set(optional_counts)
    if holds is False:
        required |= BASIC_ASSERTION_PUBLICATION_KEYS
        allowed |= BASIC_ASSERTION_PUBLICATION_KEYS
    errors: list[str] = []
    if set(value) - allowed:
        errors.append(f"{name} detail has unsupported fields")
    if required - set(value):
        errors.append(f"{name} detail omits required fields")
    reason = value.get("reason")
    offenders = value.get("offenders")
    if (not isinstance(holds, bool)
            or not isinstance(reason, str)
            or not isinstance(offenders, list)):
        errors.append(f"{name} common detail is malformed")
    for field in (*required_counts, *optional_counts):
        if field in value and not _is_count(value.get(field)):
            errors.append(f"{name} {field} is not a nonnegative integer")
    if holds is True:
        if reason != "" or offenders != []:
            errors.append(f"{name} held verdict has offender evidence")
    elif holds is False:
        count = value.get("offender_count")
        published = value.get("offenders_published")
        omitted = value.get("offenders_omitted")
        complete = value.get("offenders_complete")
        if (not reason
                or not _is_count(count)
                or not _is_count(published)
                or not _is_count(omitted)
                or not isinstance(complete, bool)
                or not isinstance(offenders, list)
                or published != len(offenders)
                or count != published + omitted
                or complete is not (omitted == 0)):
            errors.append(f"{name} offender publication relation is false")
    return errors


def _canonical_form_inventory_from_paths(
        paths: Iterable[str],
        ) -> dict[str, bool]:
    """Resolve only the two supported tracked provenance layouts.

    A set comprehension used to silently discard the 13 forms below
    ``forms/extra`` and would also have hidden a duplicate slug split across
    the two roots.  Keep the originating path until uniqueness is proved.
    """
    provenance_by_slug: dict[str, tuple[str, bool]] = {}
    for name in paths:
        parts = pathlib.PurePosixPath(name).parts
        if not parts or parts[-1] != "provenance.json":
            continue
        if (len(parts) == 3 and parts[0] == "forms"
                and parts[1] != "extra"):
            slug = parts[1]
            in_corpus = True
        elif (len(parts) == 4 and parts[:2] == ("forms", "extra")):
            slug = parts[2]
            in_corpus = False
        else:
            raise CombRefereeScopeError(
                f"unsupported tracked provenance path: {name}")
        if not slug or slug in provenance_by_slug:
            previous = provenance_by_slug.get(slug, ("<invalid>", False))[0]
            raise CombRefereeScopeError(
                f"duplicate tracked form slug {slug}: {previous}, {name}")
        provenance_by_slug[slug] = (name, in_corpus)
    if len(provenance_by_slug) != EXPECTED_FORMS:
        raise CombRefereeScopeError(
            f"tracked form corpus has {len(provenance_by_slug)}/"
            f"{EXPECTED_FORMS} slugs")
    direct_count = sum(value[1] for value in provenance_by_slug.values())
    extra_count = len(provenance_by_slug) - direct_count
    if (direct_count != EXPECTED_IN_CORPUS_FORMS
            or extra_count != EXPECTED_EXTRA_FORMS):
        raise CombRefereeScopeError(
            "tracked form root distribution is "
            f"{direct_count}/{extra_count}, expected "
            f"{EXPECTED_IN_CORPUS_FORMS}/{EXPECTED_EXTRA_FORMS}")
    return {
        slug: value[1] for slug, value in sorted(provenance_by_slug.items())}


def _canonical_form_slugs_from_paths(paths: Iterable[str]) -> frozenset[str]:
    return frozenset(_canonical_form_inventory_from_paths(paths))


def _tracked_form_paths(head: str | None = None) -> list[str]:
    revision = head or _git_text(("rev-parse", "--verify", "HEAD"))
    payload = _git((
        "ls-tree", "-r", "-z", "--name-only", revision, "--", "forms",
    ))
    names = payload.decode("utf-8", errors="strict").split("\0")
    if names and names[-1] == "":
        names.pop()
    return names


def canonical_form_inventory(head: str | None = None) -> dict[str, bool]:
    """Map every tracked slug to its direct(True)/extra(False) root."""
    return _canonical_form_inventory_from_paths(_tracked_form_paths(head))


def canonical_form_slugs(head: str | None = None) -> frozenset[str]:
    """The exact tracked corpus, independent of regenerated working bytes."""
    return frozenset(canonical_form_inventory(head))


_BATCH_SOURCE_KEYS = {"role", "file", "sha256"}
_BATCH_GUIDE_KEYS = {
    "document", "origins", "moved_from_form", "standalone_pdfs",
    "reclaimed_pt_total", "reclaimed_pct_mean", "reclaimed_pct_min",
    "reclaimed_pct_max", "note",
}
_BATCH_MOVED_GUIDE_KEYS = {
    "page", "cut_y_pt", "reclaimed_pt", "reclaimed_pct", "marker",
    "marker_pattern", "moved", "straddlers_kept_by_form", "moved_to",
}
_BATCH_MOVED_COUNTS = {
    "rules", "cells", "text_runs", "area_fills", "images",
}
_BATCH_STANDALONE_GUIDE_KEYS = {
    "file", "sha256", "linked_as", "reflowed_into",
}


def _batch_path_matches(value: Any, logical: str) -> bool:
    return (isinstance(value, str) and "\\" not in value
            and value in {logical, str(REPO / logical)})


def _batch_decimal(value: Any) -> Decimal | None:
    if not _finite_number(value):
        return None
    try:
        result = Decimal(value) if isinstance(value, int) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _batch_string_inventory(value: Any) -> bool:
    return (isinstance(value, list)
            and all(isinstance(item, str) and item for item in value)
            and value == sorted(set(value)))


def _batch_record_evidence_errors(
        record: dict[str, Any], slug: str,
        ) -> list[str]:
    """Require complete nested evidence for one successful conversion."""
    errors: list[str] = []
    pages = record.get("pages")
    html_bytes = record.get("html_bytes")
    if not _is_count(pages) or pages == 0:
        errors.append(f"batch page count is incomplete: {slug}")
    if not _is_count(html_bytes) or html_bytes == 0:
        errors.append(f"batch HTML byte count is incomplete: {slug}")
    paper = record.get("paper")
    paper_match = (re.fullmatch(
        r"([0-9]+(?:\.[0-9]+)?)x([0-9]+(?:\.[0-9]+)?)", paper)
        if isinstance(paper, str) else None)
    if (paper_match is None
            or any(Decimal(value) <= 0 for value in paper_match.groups())):
        errors.append(f"batch paper evidence is incomplete: {slug}")
    if not _batch_path_matches(
            record.get("html"), f"build/html/{slug}.html"):
        errors.append(f"batch HTML output relation is false: {slug}")

    fonts = record.get("fonts")
    if not _batch_string_inventory(fonts):
        errors.append(f"batch font inventory is malformed: {slug}")
    growables = record.get("growables")
    if not isinstance(growables, list):
        errors.append(f"batch growable inventory is malformed: {slug}")
        growables = []
    for item in growables:
        if (not isinstance(item, dict)
                or set(item) != {"page", "rows", "capacity", "pitch_pt"}
                or not _is_count(item.get("page")) or item.get("page") < 1
                or (_is_count(pages) and item.get("page") > pages)
                or not _is_count(item.get("rows")) or item.get("rows") < 1
                or not _is_count(item.get("capacity"))
                or item.get("capacity") < 1
                or not _finite_number(item.get("pitch_pt"))
                or item.get("pitch_pt") <= 0):
            errors.append(f"batch growable evidence is malformed: {slug}")
            break

    raw_sources = record.get("sources")
    sources = raw_sources if isinstance(raw_sources, list) else []
    if (not isinstance(raw_sources, list)
            or any(not isinstance(source, dict)
                   or set(source) != _BATCH_SOURCE_KEYS
                   or not isinstance(source.get("role"), str)
                   or source.get("role") not in {"form", "guide"}
                   or not isinstance(source.get("file"), str)
                   or not source.get("file")
                   or not _is_sha256(source.get("sha256"))
                   for source in sources)):
        errors.append(f"batch source inventory is malformed: {slug}")
    form_sources = [
        source for source in sources
        if isinstance(source, dict) and source.get("role") == "form"
    ]
    guide_sources = [
        source for source in sources
        if isinstance(source, dict) and source.get("role") == "guide"
    ]
    if (len(form_sources) != 1
            or form_sources[0].get("file") != record.get("source_file")
            or form_sources[0].get("sha256") != record.get("sha256")):
        errors.append(f"batch form source relation is false: {slug}")
    source_files = [
        source.get("file") for source in sources
        if isinstance(source, dict)
        and isinstance(source.get("file"), str)
    ]
    if (len(source_files) != len(sources)
            or len(set(source_files)) != len(source_files)):
        errors.append(f"batch source filenames are duplicated: {slug}")

    detected = record.get("guide_detected")
    if (not isinstance(detected, dict)
            or set(detected) != {"inline_pages", "standalone_pdfs"}):
        errors.append(f"batch guide detection is malformed: {slug}")
        inline_pages: list[Any] = []
        standalone_names: list[Any] = []
    else:
        inline_pages = detected.get("inline_pages")
        standalone_names = detected.get("standalone_pdfs")
        if (not isinstance(inline_pages, list)
                or any(not _is_count(page) or page < 1
                       or (_is_count(pages) and page > pages)
                       for page in inline_pages)
                or inline_pages != sorted(set(inline_pages))):
            errors.append(f"batch inline-guide pages are malformed: {slug}")
            inline_pages = []
        if not _batch_string_inventory(standalone_names):
            errors.append(
                f"batch standalone-guide inventory is malformed: {slug}")
            standalone_names = []
    if standalone_names != [source.get("file") for source in guide_sources]:
        errors.append(f"batch standalone-guide sources are unbound: {slug}")

    guide_build = record.get("guide_build")
    if (not isinstance(guide_build, dict)
            or set(guide_build) != {"plan", "html", "pdfs"}):
        errors.append(f"batch guide-build evidence is malformed: {slug}")
        guide_build = {}
    if not _batch_path_matches(
            guide_build.get("plan"), f"build/guides/{slug}.guide.json"):
        errors.append(f"batch guide plan relation is false: {slug}")
    guide_pdfs = guide_build.get("pdfs")
    if (not _batch_string_inventory(guide_pdfs)
            or [pathlib.PurePosixPath(item).name for item in guide_pdfs]
            != standalone_names):
        errors.append(f"batch guide PDF relation is false: {slug}")

    guide_source_irs = record.get("guide_source_irs")
    if not _batch_string_inventory(guide_source_irs):
        errors.append(f"batch guide IR inventory is malformed: {slug}")
        guide_source_irs = []
    expected_guide_irs = [
        f"build/ir/guides/{slug}.guide-{index}.ir.json"
        for index in range(1, len(standalone_names) + 1)
    ]
    if (len(guide_source_irs) != len(expected_guide_irs)
            or any(not _batch_path_matches(value, expected)
                   for value, expected in zip(
                       guide_source_irs, expected_guide_irs))):
        errors.append(f"batch guide IR relation is false: {slug}")

    font_plans = record.get("font_plans")
    if (not _batch_string_inventory(font_plans)
            or len(font_plans) != 1
            or not _batch_path_matches(
                font_plans[0] if font_plans else None,
                f"build/fonts/{slug}.fontplan.json")):
        errors.append(f"batch font-plan relation is false: {slug}")

    raw_guide = record.get("guide")
    has_guide = bool(inline_pages or standalone_names)
    if has_guide is not isinstance(raw_guide, dict):
        errors.append(f"batch guide presence relation is false: {slug}")
    if not has_guide:
        if raw_guide is not None or guide_build.get("html") is not None:
            errors.append(f"batch absent guide relation is false: {slug}")
        return errors
    if (not isinstance(raw_guide, dict)
            or set(raw_guide) != _BATCH_GUIDE_KEYS):
        errors.append(f"batch guide publication is malformed: {slug}")
        return errors
    if (raw_guide.get("document") != "guide.html"
            or raw_guide.get("origins") != [
                *(["inline-region"] if inline_pages else []),
                *(["standalone-pdf"] if standalone_names else []),
            ]
            or not isinstance(raw_guide.get("note"), str)
            or not raw_guide.get("note")
            or not _batch_path_matches(
                guide_build.get("html"),
                f"build/html/{slug}.guide.html")):
        errors.append(f"batch guide metadata relation is false: {slug}")
    moved = raw_guide.get("moved_from_form")
    if (not isinstance(moved, list) or len(moved) != len(inline_pages)
            or [item.get("page") for item in moved
                if isinstance(item, dict)] != inline_pages):
        errors.append(f"batch moved-guide inventory is false: {slug}")
        moved = []
    valid_moved: list[dict[str, Any]] = []
    for item in moved:
        counts = item.get("moved") if isinstance(item, dict) else None
        cut_y = item.get("cut_y_pt") if isinstance(item, dict) else None
        reclaimed = (
            item.get("reclaimed_pt") if isinstance(item, dict) else None)
        reclaimed_pct = (
            item.get("reclaimed_pct") if isinstance(item, dict) else None)
        if (not isinstance(item, dict)
                or set(item) != _BATCH_MOVED_GUIDE_KEYS
                or not _is_count(item.get("page"))
                or item.get("page") < 1
                or _batch_decimal(cut_y) is None
                or cut_y < 0
                or _batch_decimal(reclaimed) is None
                or reclaimed < 0
                or _batch_decimal(reclaimed_pct) is None
                or not 0 <= reclaimed_pct <= 100
                or not isinstance(item.get("marker"), str)
                or not item.get("marker").strip()
                or not isinstance(item.get("marker_pattern"), str)
                or not item.get("marker_pattern").strip()
                or not isinstance(counts, dict)
                or set(counts) != _BATCH_MOVED_COUNTS
                or any(not _is_count(counts.get(key))
                       for key in _BATCH_MOVED_COUNTS)
                or not _is_count(item.get("straddlers_kept_by_form"))
                or item.get("moved_to") != "guide.html"):
            errors.append(f"batch moved-guide evidence is malformed: {slug}")
            break
        valid_moved.append(item)
    standalone = raw_guide.get("standalone_pdfs")
    if not isinstance(standalone, list) or len(standalone) != len(guide_sources):
        errors.append(f"batch standalone-guide publication is false: {slug}")
        standalone = []
    for item, source in zip(standalone, guide_sources):
        if (not isinstance(item, dict)
                or set(item) != _BATCH_STANDALONE_GUIDE_KEYS
                or item.get("file") != source.get("file")
                or item.get("sha256") != source.get("sha256")
                or item.get("linked_as") != f"guides/{source.get('file')}"
                or item.get("reflowed_into") != "guide.html"):
            errors.append(
                f"batch standalone-guide evidence is malformed: {slug}")
            break
    summary_keys = (
        "reclaimed_pt_total", "reclaimed_pct_mean",
        "reclaimed_pct_min", "reclaimed_pct_max",
    )
    summary_values = {key: raw_guide.get(key) for key in summary_keys}
    for key, value in summary_values.items():
        if (not _finite_number(value) or value < 0
                or ("pct" in key and value > 100)):
            errors.append(f"batch guide summary is malformed: {slug}/{key}")
    if len(valid_moved) == len(moved):
        reclaimed_values = [
            _batch_decimal(item["reclaimed_pt"]) for item in valid_moved]
        pct_values = [
            _batch_decimal(item["reclaimed_pct"]) for item in valid_moved]
        if all(value is not None for value in [*reclaimed_values, *pct_values]):
            reclaimed_decimals = [
                value for value in reclaimed_values if value is not None]
            pct_decimals = [value for value in pct_values if value is not None]
            expected_summaries = {
                "reclaimed_pt_total": sum(
                    reclaimed_decimals, Decimal(0)).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_EVEN),
                "reclaimed_pct_mean": (
                    (sum(pct_decimals, Decimal(0)) / len(pct_decimals))
                    .quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)
                    if pct_decimals else Decimal(0)),
                "reclaimed_pct_min": (
                    min(pct_decimals) if pct_decimals else Decimal(0)),
                "reclaimed_pct_max": (
                    max(pct_decimals) if pct_decimals else Decimal(0)),
            }
            for key, expected in expected_summaries.items():
                observed = _batch_decimal(summary_values[key])
                if observed is None or observed != expected:
                    errors.append(
                        f"batch guide summary is not derived: {slug}/{key}")
    return errors


def batch_report_errors(
        data: Any, expected_slugs: frozenset[str],
        expected_in_corpus: dict[str, bool] | None = None,
        ) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, list):
        return ["batch report is not a list"]
    slugs: list[str] = []
    for index, record in enumerate(data):
        if not isinstance(record, dict) or set(record) != BATCH_RECORD_KEYS:
            errors.append(f"batch record schema is unsupported: {index}")
            continue
        slug = record.get("slug")
        code = record.get("code")
        revision = record.get("revision")
        variant = record.get("variant")
        if not all(isinstance(value, str) for value in (
                slug, code, revision, variant)):
            errors.append(f"batch record identity is malformed: {index}")
            continue
        expected_slug = f"{code}-{revision}{f'-{variant}' if variant else ''}".lower()
        if not slug or slug != expected_slug:
            errors.append(f"batch record identity relation is false: {slug}")
        slugs.append(slug)
        in_corpus = record.get("in_corpus")
        if (not isinstance(in_corpus, bool)
                or not isinstance(record.get("source_file"), str)
                or not record["source_file"]
                or not _is_sha256(record.get("sha256"))
                or record.get("stage_failed") is not None
                or record.get("error") is not None):
            errors.append(f"batch record did not complete: {slug}")
        if (expected_in_corpus is not None and slug in expected_in_corpus
                and in_corpus is not expected_in_corpus[slug]):
            errors.append(f"batch form root classification is false: {slug}")
        for key in (
                "images_extracted", "pages", "rules", "text_runs", "images",
                "cells", "comb_cells", "html_bytes"):
            if not _is_count(record.get(key)):
                errors.append(f"batch record count is malformed: {slug}/{key}")
        if not isinstance(record.get("uniform_paper"), bool):
            errors.append(f"batch uniform-paper evidence is malformed: {slug}")
        errors.extend(_batch_record_evidence_errors(record, slug))
    if len(slugs) != len(set(slugs)):
        errors.append("batch report contains duplicate slugs")
    if set(slugs) != set(expected_slugs):
        errors.append("batch report does not match the exact tracked slug corpus")
    return errors


def _fresh_batch_report(
        path: pathlib.Path, expected_slugs: frozenset[str],
        expected_inventory: dict[str, bool],
        ) -> tuple[dict[str, Any], bytes]:
    record = _stable_file_record(path, "build/batch-report.json")
    payload = path.read_bytes()
    if sha256_bytes(payload) != record["sha256"]:
        raise CombRefereeScopeError("batch report changed while validating")
    try:
        data = json.loads(payload)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise CombRefereeScopeError(f"batch report is malformed: {error}") from error
    errors = batch_report_errors(data, expected_slugs, expected_inventory)
    if errors:
        raise CombRefereeScopeError("; ".join(errors[:5]))
    record["form_count"] = len(data)
    record["slug_sha256"] = canonical_digest(sorted(expected_slugs))
    return record, payload


AUDIT_PRODUCER_KEYS = {
    "file", "bytes", "sha256", "dependencies",
    "dependency_execution_bound", "audit_execution_bound",
    "assertion_producer_bound", "roundtrip_runtime_bound_in_record",
    "standalone_attestation_complete", "incomplete_reason",
}
AUDIT_PRODUCER_DEPENDENCY_KEYS = {
    "file", "bytes", "sha256", "loaded_origin",
    "executed_from_snapshotted_source",
}
AUDIT_RUNTIME_KEYS = {
    "python", "pymupdf", "loaded_application_files", "application_closure",
    "stdlib_and_system_shared_libraries_bound", "scope_complete",
    "incomplete_reason",
}
AUDIT_RUNTIME_FILES_KEYS = {
    "algorithm", "files", "bytes", "tree_sha256", "members",
    "validated_before_after",
}
AUDIT_APPLICATION_CLOSURE_KEYS = {
    "scope", "algorithm", "bytecode_caches_excluded", "exclusion_reason",
    "packages", "modules", "native_libraries", "unbound_modules",
    "validated_before_after", "complete",
}
AUDIT_APPLICATION_CLOSURE_SCOPE = (
    "interpreter-binaries-and-application-package-trees-v1")
AUDIT_TREE_CLOSURE_ALGORITHM = (
    "sha256(canonical-json(path,type,bytes,digest))")
AUDIT_APPLICATION_PACKAGE_KEYS = {
    "logical_root", "algorithm", "files", "symlinks", "bytes", "tree_sha256",
}


def _audit_application_closure_errors(
        closure: Any, members: Sequence[tuple[str, int, str]], slug: str,
        ) -> list[str]:
    """Shape and internal relations only; comb_referee.py does the rehash.

    The gate deliberately does not re-derive these digests: the independent
    rehash is the referee's job and binding the referee's report is this
    gate's. What it does insist on is that the record cannot be internally
    inconsistent -- an unaccounted module, a completeness flag that disagrees
    with its own unbound list, or a module inventory that does not match the
    loaded-file inventory it was derived from.
    """
    errors: list[str] = []
    if not isinstance(closure, dict) or set(
            closure) != AUDIT_APPLICATION_CLOSURE_KEYS:
        return [f"audit application closure is malformed: {slug}"]
    if (closure.get("scope") != AUDIT_APPLICATION_CLOSURE_SCOPE
            or closure.get("algorithm") != AUDIT_TREE_CLOSURE_ALGORITHM
            or closure.get("bytecode_caches_excluded") is not True
            or not isinstance(closure.get("exclusion_reason"), str)
            or not closure.get("exclusion_reason")
            or closure.get("validated_before_after") is not True):
        errors.append(f"audit application closure declaration is false: {slug}")
    packages = closure.get("packages")
    if (not isinstance(packages, list) or not packages
            or not all(
                isinstance(item, dict)
                and set(item) == AUDIT_APPLICATION_PACKAGE_KEYS
                and item.get("algorithm") == AUDIT_TREE_CLOSURE_ALGORITHM
                and isinstance(item.get("logical_root"), str)
                and item.get("logical_root")
                and _is_count(item.get("files")) and item.get("files") > 0
                and _is_count(item.get("symlinks"))
                and _is_count(item.get("bytes")) and item.get("bytes") > 0
                and _is_sha256(item.get("tree_sha256"))
                for item in packages)):
        errors.append(f"audit application package trees are malformed: {slug}")
        packages = []
    roots = [
        item.get("logical_root") for item in packages
        if isinstance(item, dict)
    ]
    if roots != sorted(set(roots)):
        errors.append(f"audit application package roots are not exact: {slug}")
    native = closure.get("native_libraries")
    if (not isinstance(native, list) or not native
            or not all(
                isinstance(item, dict)
                and set(item) == {"file", "bytes", "sha256"}
                and isinstance(item.get("file"), str)
                and item.get("file").split("/", 1)[0] in roots
                and _is_count(item.get("bytes")) and item.get("bytes") > 0
                and _is_sha256(item.get("sha256"))
                for item in native)
            or [item.get("file") for item in native]
            != sorted(item.get("file") for item in native)):
        errors.append(
            f"audit bundled native library inventory is malformed: {slug}")
    modules = closure.get("modules")
    if (not isinstance(modules, list)
            or not all(
                isinstance(item, dict)
                and set(item) == {"module", "file", "bytes", "sha256"}
                and isinstance(item.get("module"), str)
                and item.get("module")
                and isinstance(item.get("file"), str)
                and item.get("file").split("/", 1)[0] in roots
                and _is_count(item.get("bytes"))
                and _is_sha256(item.get("sha256"))
                for item in modules)
            or [item.get("file") for item in modules]
            != sorted(item.get("file") for item in modules)):
        errors.append(f"audit application module inventory is malformed: {slug}")
        return errors
    unbound = closure.get("unbound_modules")
    if (not isinstance(unbound, list)
            or unbound != sorted(unbound)
            or not all(isinstance(item, str) and item for item in unbound)):
        errors.append(f"audit unbound module inventory is malformed: {slug}")
        return errors
    if closure.get("complete") is not (not unbound):
        errors.append(
            f"audit application closure completeness relation is false: {slug}")
    loaded_modules = {
        logical[len("module/"):]: (size, digest)
        for logical, size, digest in members
        if logical.startswith("module/")
    }
    published = {item["module"]: (item["bytes"], item["sha256"])
                 for item in modules}
    accounted = set(published) | {
        item[len("module/"):] for item in unbound
        if item.startswith("module/")
    }
    if (len(published) != len(modules)
            or accounted != set(loaded_modules)
            or any(published[name] != loaded_modules[name]
                   for name in published if name in loaded_modules)):
        errors.append(
            f"audit application closure does not account for the loaded "
            f"application modules: {slug}")
    return errors
AUDIT_INPUT_ROLES = {"ir", "layout", "html", "guide", "guide_html", "source_pdf"}
AUDIT_INPUT_FILE_KEYS = {"file", "required", "present", "bytes", "sha256"}
AUDIT_SOURCE_INPUT_KEYS = {
    *AUDIT_INPUT_FILE_KEYS, "logical_identity", "path", "expected_sha256",
}
AUDIT_RENDER_KEYS = {
    "entrypoint", "dependencies", "errors", "complete", "network_policy",
}
AUDIT_RENDER_DEPENDENCY_KEYS = {
    "path", "mime_type", "present", "bytes", "sha256", "kinds", "referrers",
}


def _audit_input_manifest_shape_errors(
        manifest: Any, slug: str,
        ) -> list[str]:
    errors: list[str] = []
    if not isinstance(manifest, dict) or set(manifest) != AUDIT_INPUT_MANIFEST_KEYS:
        return [f"audit input manifest schema is unsupported: {slug}"]
    if (manifest.get("schema") != "formgen-audit-input-manifest-v1"
            or manifest.get("algorithm") != "sha256"
            or manifest.get("inputs_complete") is not True
            or manifest.get("attestation_complete") is not True
            or manifest.get("enforceable") is not True
            or manifest.get("complete") is not True
            or manifest.get("missing_required") != []):
        errors.append(f"audit input manifest verdict is malformed: {slug}")

    producer = manifest.get("producer")
    if not isinstance(producer, dict) or set(producer) != AUDIT_PRODUCER_KEYS:
        errors.append(f"audit producer manifest is malformed: {slug}")
    else:
        dependencies = producer.get("dependencies")
        if (producer.get("file") != "tools/formgen/audit.py"
                or not _is_count(producer.get("bytes"))
                or not _is_sha256(producer.get("sha256"))
                or producer.get("dependency_execution_bound") is not True
                or producer.get("audit_execution_bound") is not False
                or producer.get("assertion_producer_bound") is not False
                or producer.get("roundtrip_runtime_bound_in_record") is not False
                or producer.get("standalone_attestation_complete") is not False
                or not isinstance(producer.get("incomplete_reason"), str)
                or not producer.get("incomplete_reason")
                or not isinstance(dependencies, list)):
            errors.append(f"audit producer relations are malformed: {slug}")
        else:
            names: list[str] = []
            for dependency in dependencies:
                if (not isinstance(dependency, dict)
                        or set(dependency) != AUDIT_PRODUCER_DEPENDENCY_KEYS):
                    errors.append(
                        f"audit producer dependency is malformed: {slug}")
                    continue
                name = dependency.get("file")
                names.append(name if isinstance(name, str) else "")
                if (not _is_count(dependency.get("bytes"))
                        or not _is_sha256(dependency.get("sha256"))
                        or dependency.get("loaded_origin") != name
                        or dependency.get(
                            "executed_from_snapshotted_source") is not True):
                    errors.append(
                        f"audit producer dependency relation is false: {slug}")
            if names != ["tools/formgen/extract.py", "tools/formgen/verify.py"]:
                errors.append(
                    f"audit producer dependency inventory is false: {slug}")

    runtime = manifest.get("runtime")
    if not isinstance(runtime, dict) or set(runtime) != AUDIT_RUNTIME_KEYS:
        errors.append(f"audit base runtime manifest is malformed: {slug}")
    else:
        python = runtime.get("python")
        pymupdf = runtime.get("pymupdf")
        loaded = runtime.get("loaded_application_files")
        if (not isinstance(python, dict)
                or set(python) != {"implementation", "version", "cache_tag"}
                or any(not isinstance(python.get(key), str)
                       or not python.get(key)
                       for key in python)
                or not isinstance(pymupdf, dict)
                or set(pymupdf) != {"package_version", "version_bind"}
                or any(not isinstance(pymupdf.get(key), str)
                       or not pymupdf.get(key)
                       for key in pymupdf)
                or pymupdf.get("package_version") != pymupdf.get("version_bind")
                or runtime.get(
                    "stdlib_and_system_shared_libraries_bound") is not False
                or runtime.get("scope_complete") is not False
                or not isinstance(runtime.get("incomplete_reason"), str)
                or not runtime.get("incomplete_reason")):
            errors.append(f"audit base runtime identity is malformed: {slug}")
        if not isinstance(loaded, dict) or set(loaded) != AUDIT_RUNTIME_FILES_KEYS:
            errors.append(f"audit loaded runtime files are malformed: {slug}")
        else:
            members = loaded.get("members")
            member_tuples: list[tuple[str, int, str]] = []
            if isinstance(members, list):
                for member in members:
                    if (not isinstance(member, dict)
                            or set(member) != {"file", "bytes", "sha256"}
                            or not isinstance(member.get("file"), str)
                            or not member.get("file")
                            or not _is_count(member.get("bytes"))
                            or not _is_sha256(member.get("sha256"))):
                        errors.append(
                            f"audit runtime member is malformed: {slug}")
                        continue
                    member_tuples.append((
                        member["file"], member["bytes"], member["sha256"]))
            else:
                errors.append(f"audit runtime member inventory is malformed: {slug}")
            try:
                runtime_payload = json.dumps(
                    member_tuples, separators=(",", ":")).encode("ascii")
            except (UnicodeEncodeError, ValueError, OverflowError):
                runtime_payload = b""
                errors.append(
                    f"audit runtime member canonicalization failed: {slug}")
            if (loaded.get("algorithm")
                    != "sha256(canonical-json(logical-file,bytes,sha256))"
                    or loaded.get("validated_before_after") is not True
                    or loaded.get("files") != len(member_tuples)
                    or loaded.get("bytes")
                    != sum(member[1] for member in member_tuples)
                    or loaded.get("tree_sha256")
                    != sha256_bytes(runtime_payload)
                    or len({member[0] for member in member_tuples})
                    != len(member_tuples)
                    or member_tuples != sorted(
                        member_tuples, key=lambda item: item[0])):
                errors.append(f"audit runtime member relation is false: {slug}")
            errors.extend(_audit_application_closure_errors(
                runtime.get("application_closure"), member_tuples, slug))

    inputs = manifest.get("inputs")
    if not isinstance(inputs, dict) or set(inputs) != AUDIT_INPUT_ROLES:
        errors.append(f"audit input role inventory is malformed: {slug}")
    else:
        for role, value in inputs.items():
            keys = (AUDIT_SOURCE_INPUT_KEYS
                    if role == "source_pdf" else AUDIT_INPUT_FILE_KEYS)
            required = role != "guide_html"
            if (not isinstance(value, dict) or set(value) != keys
                    or value.get("required") is not required
                    or not isinstance(value.get("present"), bool)
                    or not isinstance(value.get("file"), str)
                    or not value.get("file")):
                errors.append(f"audit input role is malformed: {slug}/{role}")
                continue
            if value.get("present") is True:
                if (not _is_count(value.get("bytes"))
                        or not _is_sha256(value.get("sha256"))):
                    errors.append(
                        f"audit present input identity is malformed: {slug}/{role}")
            elif (required or value.get("bytes") is not None
                  or value.get("sha256") is not None):
                errors.append(
                    f"audit missing input relation is false: {slug}/{role}")
            if role == "source_pdf" and (
                    not isinstance(value.get("logical_identity"), str)
                    or not value.get("logical_identity")
                    or not isinstance(value.get("path"), str)
                    or not value.get("path")
                    or not _is_sha256(value.get("expected_sha256"))):
                errors.append(f"audit source input is malformed: {slug}")

    render = manifest.get("render")
    if not isinstance(render, dict) or set(render) != AUDIT_RENDER_KEYS:
        errors.append(f"audit render manifest is malformed: {slug}")
    else:
        dependencies = render.get("dependencies")
        if (render.get("entrypoint") != f"{slug}.html"
                or render.get("errors") != []
                or render.get("complete") is not True
                or render.get("network_policy")
                != "deny-except-retained-relative-resources-and-inline-data"
                or not isinstance(dependencies, list)):
            errors.append(f"audit render relation is malformed: {slug}")
        else:
            paths: list[str] = []
            for dependency in dependencies:
                if (not isinstance(dependency, dict)
                        or set(dependency) != AUDIT_RENDER_DEPENDENCY_KEYS):
                    errors.append(f"audit render dependency is malformed: {slug}")
                    continue
                path = dependency.get("path")
                paths.append(path if isinstance(path, str) else "")
                if (not isinstance(path, str) or not path
                        or pathlib.PurePosixPath(path).is_absolute()
                        or pathlib.PurePosixPath(path).as_posix() != path
                        or "\\" in path
                        or ".." in pathlib.PurePosixPath(path).parts
                        or dependency.get("present") is not True
                        or not _is_count(dependency.get("bytes"))
                        or not _is_sha256(dependency.get("sha256"))
                        or not isinstance(dependency.get("mime_type"), str)
                        or not dependency.get("mime_type")
                        or not isinstance(dependency.get("kinds"), list)
                        or not dependency.get("kinds")
                        or not all(isinstance(item, str) and item
                                   for item in dependency.get("kinds", []))
                        or (all(isinstance(item, str)
                                for item in dependency.get("kinds", []))
                            and dependency.get("kinds")
                            != sorted(set(dependency.get("kinds", []))))
                        or not isinstance(dependency.get("referrers"), list)
                        or not dependency.get("referrers")
                        or not all(isinstance(item, str) and item
                                   for item in dependency.get("referrers", []))
                        or (all(isinstance(item, str)
                                for item in dependency.get("referrers", []))
                            and dependency.get("referrers")
                            != sorted(set(dependency.get("referrers", []))))):
                    errors.append(
                        f"audit render dependency relation is false: {slug}")
            if paths != sorted(paths) or len(paths) != len(set(paths)):
                errors.append(f"audit render dependency order is false: {slug}")
    return errors


def _audit_roundtrip_payload_errors(
        record: dict[str, Any], slug: str,
        ) -> list[str]:
    """Require the complete successful browser/print/re-extraction record."""
    errors: list[str] = []
    runtime = record.get("roundtrip_runtime")
    requests = record.get("render_requests")
    candidate = record.get("candidate_pdf")
    runtime_keys = {
        "mode", "playwright_package_version", "dependency_closure",
        "chromium", "same_resolution_session_used_for_render",
        "dependency_closure_validated_before_after",
        "system_shared_libraries_bound", "native_host_environment_bound",
        "scope", "scope_complete", "incomplete_reason",
        "live_browser_version", "explicit_executable_path_used",
        "launch_args", "service_workers", "browser_context_offline",
        "websocket_policy", "request_policy",
        "playwright_operation_timeout_ms", "hard_deadline_seconds",
        "hard_deadline_enforced_by", "deadline_cleanup_policy",
    }
    if not isinstance(runtime, dict) or set(runtime) != runtime_keys:
        errors.append(f"audit roundtrip runtime is malformed: {slug}")
        runtime = {}
    else:
        deadline = runtime.get("hard_deadline_seconds")
        if (runtime.get("mode") != "playwright-exact-executable"
                or not isinstance(runtime.get("playwright_package_version"), str)
                or not runtime.get("playwright_package_version")
                or runtime.get(
                    "same_resolution_session_used_for_render") is not True
                or runtime.get(
                    "dependency_closure_validated_before_after") is not True
                or runtime.get("explicit_executable_path_used") is not True
                or runtime.get("browser_context_offline") is not True
                or runtime.get("service_workers") != "block"
                or runtime.get("websocket_policy")
                != "record-and-leave-unconnected"
                or runtime.get("request_policy") != "formgen-snapshot-only-v1"
                or not _finite_number(deadline) or deadline != 60.0
                or runtime.get("playwright_operation_timeout_ms") != 120000
                or isinstance(
                    runtime.get("playwright_operation_timeout_ms"), bool)
                or runtime.get("hard_deadline_enforced_by")
                != "isolated-render-worker-process-v1"
                or runtime.get("deadline_cleanup_policy")
                != "kill-worker-and-chromium-process-group"
                or runtime.get("system_shared_libraries_bound") is not False
                or runtime.get("native_host_environment_bound") is not False
                or runtime.get("scope") != AUDIT_ROUNDTRIP_SCOPE
                or runtime.get("scope_complete") is not False
                or not isinstance(runtime.get("incomplete_reason"), str)
                or not runtime.get("incomplete_reason")
                or runtime.get("launch_args") != AUDIT_ROUNDTRIP_LAUNCH_ARGS):
            errors.append(f"audit roundtrip execution relation is false: {slug}")
        closure = runtime.get("dependency_closure")
        if (not isinstance(closure, dict)
                or set(closure) != {
                    "logical_root", "algorithm", "files", "symlinks",
                    "bytes", "tree_sha256"}
                or closure.get("logical_root") != "playwright"
                or closure.get("algorithm")
                != "sha256(canonical-json(path,type,bytes,digest))"
                or any(not _is_count(closure.get(key))
                       for key in ("files", "symlinks", "bytes"))
                or not _is_sha256(closure.get("tree_sha256"))):
            errors.append(f"audit roundtrip dependency closure is malformed: {slug}")
        chromium = runtime.get("chromium")
        chromium_file = chromium.get("file") if isinstance(chromium, dict) else None
        if (not isinstance(chromium, dict)
                or set(chromium) != {
                    "file", "bytes", "sha256", "version_output"}
                or not isinstance(chromium_file, str)
                or not chromium_file.startswith("playwright/")
                or posixpath.normpath(chromium_file) != chromium_file
                or ".." in pathlib.PurePosixPath(chromium_file).parts
                or not _is_count(chromium.get("bytes"))
                or chromium.get("bytes") == 0
                or not _is_sha256(chromium.get("sha256"))
                or not isinstance(chromium.get("version_output"), str)
                or not chromium.get("version_output")
                or not isinstance(runtime.get("live_browser_version"), str)
                or not runtime.get("live_browser_version")
                or chromium.get("version_output", "").rsplit(" ", 1)[-1]
                != runtime.get("live_browser_version")):
            errors.append(f"audit roundtrip Chromium identity is malformed: {slug}")

    request_keys = {
        "policy", "synthetic_origin", "fulfilled", "fulfilled_requests",
        "blocked", "blocked_requests", "blocked_websockets",
        "all_requests_from_retained_closure",
    }
    if not isinstance(requests, dict) or set(requests) != request_keys:
        errors.append(f"audit roundtrip request manifest is malformed: {slug}")
    else:
        manifest = record.get("input_manifest")
        render = manifest.get("render") if isinstance(manifest, dict) else None
        dependencies = render.get("dependencies") if isinstance(render, dict) else None
        dependency_paths = [
            item.get("path") for item in dependencies
            if isinstance(item, dict)] if isinstance(dependencies, list) else []
        entrypoint = render.get("entrypoint") if isinstance(render, dict) else None
        paths_valid = bool(
            isinstance(dependencies, list)
            and isinstance(entrypoint, str) and entrypoint
            and all(isinstance(item, str) and item for item in dependency_paths)
            and len(dependency_paths) == len(dependencies)
            and dependency_paths == sorted(dependency_paths)
            and len(dependency_paths) == len(set(dependency_paths)))
        retained = ({entrypoint, *dependency_paths} if paths_valid else set())
        fulfilled = requests.get("fulfilled")
        derived = bool(
            paths_valid
            and isinstance(fulfilled, list) and fulfilled
            and all(isinstance(item, str) and item for item in fulfilled)
            and fulfilled == sorted(set(fulfilled))
            and set(fulfilled) == retained
            and requests.get("fulfilled_requests") == len(fulfilled)
            and not isinstance(requests.get("fulfilled_requests"), bool)
            and requests.get("blocked") == []
            and requests.get("blocked_requests") == 0
            and not isinstance(requests.get("blocked_requests"), bool)
            and requests.get("blocked_websockets") == [])
        if (requests.get("policy") != "formgen-snapshot-only-v1"
                or requests.get("synthetic_origin") != "https://formgen.invalid"
                or requests.get("all_requests_from_retained_closure") is not derived
                or not derived):
            errors.append(f"audit roundtrip request closure is false: {slug}")

    candidate_keys = {
        "bytes", "sha256", "retained_exact_bytes",
        "chromium_returned_in_memory", "normalization", "materialization",
        "expected_sha256_passed_to_extractor",
        "validated_before_after_extraction", "candidate_ir_sha256",
        "candidate_ir_digest_scope",
    }
    if not isinstance(candidate, dict) or set(candidate) != candidate_keys:
        errors.append(f"audit candidate PDF manifest is malformed: {slug}")
    else:
        if (not _is_count(candidate.get("bytes"))
                or candidate.get("bytes") == 0
                or not _is_sha256(candidate.get("sha256"))
                or not _is_sha256(candidate.get("candidate_ir_sha256"))
                or candidate.get("retained_exact_bytes") is not True
                or candidate.get("chromium_returned_in_memory") is not True
                or candidate.get(
                    "expected_sha256_passed_to_extractor") is not True
                or candidate.get(
                    "validated_before_after_extraction") is not True
                or candidate.get("materialization")
                != AUDIT_CANDIDATE_MATERIALIZATION
                or candidate.get("candidate_ir_digest_scope")
                != "source-and-generator-removed"):
            errors.append(f"audit candidate PDF provenance is malformed: {slug}")
        normalization = candidate.get("normalization")
        if (not isinstance(normalization, dict)
                or set(normalization) != {
                    "algorithm", "fields_normalized", "replacement",
                    "xref_offsets_preserved"}
                or normalization.get("algorithm")
                != "fixed-width-creation-modification-date-v1"
                or normalization.get("fields_normalized") != 2
                or isinstance(normalization.get("fields_normalized"), bool)
                or normalization.get("replacement")
                != AUDIT_PDF_NORMALIZATION_REPLACEMENT
                or normalization.get("xref_offsets_preserved") is not True):
            errors.append(f"audit candidate PDF normalization is malformed: {slug}")
    if (record.get("measured") is not True
            or record.get("hard_failure") is not None
            or record.get("error") is not None
            or record.get("status") != "ok"
            or "roundtrip_liveness" in record):
        errors.append(f"audit roundtrip success state is malformed: {slug}")
    return errors


def full_audit_payload_errors(
        data: Any, expected_slugs: frozenset[str],
        ) -> list[str]:
    """Reject a slug-only JSON fixture; require producer-shaped relations."""
    errors: list[str] = []
    if not isinstance(data, list):
        return ["audit report is not a list"]
    slugs: list[str] = []
    for index, record in enumerate(data):
        if not isinstance(record, dict):
            errors.append(f"audit record is not an object: {index}")
            continue
        slug = record.get("slug")
        if not isinstance(slug, str) or not slug:
            errors.append(f"audit record has no slug: {index}")
            continue
        slugs.append(slug)
        manifest = record.get("input_manifest")
        assertions = record.get("assertions")
        provenance = record.get("provenance_validation")
        attestation = record.get("attestation")
        errors.extend(_audit_input_manifest_shape_errors(manifest, slug))
        if (not isinstance(provenance, dict)
                or set(provenance) != {
                    "validated_before", "validated_after", "error"}
                or provenance.get("validated_before") is not True
                or provenance.get("validated_after") is not True
                or provenance.get("error") is not None):
            errors.append(f"audit provenance relation is malformed: {slug}")
        # `complete` here is the audit's claim over its published application
        # closure, and it must be exactly the conjunction it is derived from --
        # a record that claims it while any of the three inputs is false is
        # malformed, not merely optimistic. The scope boundaries it never
        # covered stay published in `declared_out_of_scope`, which is why that
        # list is still required to be non-empty on a fully green record.
        if (not isinstance(attestation, dict)
                or set(attestation) != AUDIT_ATTESTATION_KEYS
                or attestation.get("inputs_complete") is not True
                or attestation.get("producer_execution_bound") is not False
                or attestation.get("base_runtime_scope_complete") is not False
                or attestation.get(
                    "roundtrip_runtime_scope_complete") is not False
                or attestation.get(
                    "application_closure_complete") is not True
                or attestation.get("validated_before_after") is not True
                or attestation.get("complete") is not True
                or attestation.get("enforceable") is not True
                or attestation.get("incomplete_reasons") != []
                or not isinstance(
                    attestation.get("declared_out_of_scope"), list)
                or not attestation.get("declared_out_of_scope")
                or not all(
                    isinstance(item, str) and item
                    for item in attestation.get("declared_out_of_scope", []))
                or not isinstance(attestation.get("future_gate_required"), str)
                or not attestation.get("future_gate_required")):
            errors.append(f"audit attestation is malformed: {slug}")
        if (not isinstance(assertions, dict)
                or set(assertions) != set(REQUIRED_ASSERTIONS)):
            errors.append(f"audit assertion inventory is malformed: {slug}")
            continue
        held_count = 0
        for key in REQUIRED_ASSERTIONS:
            detail = assertions.get(key)
            if (not isinstance(detail, dict)
                    or not isinstance(detail.get("holds"), bool)
                    or record.get(key) is not detail.get("holds")):
                errors.append(f"audit assertion relation is false: {slug}/{key}")
                continue
            if key != "comb_slots_match_printed":
                errors.extend(
                    f"audit assertion publication is invalid: {slug}: {item}"
                    for item in _basic_assertion_detail_errors(key, detail))
            if detail["holds"]:
                held_count += 1
        if record.get("assertions_held") != held_count:
            errors.append(f"audit assertion total is false: {slug}")
        comb_detail = assertions.get("comb_slots_match_printed")
        try:
            _normalise_outer_comb_assertion(comb_detail)
        except CombRefereeScopeError as error:
            errors.append(f"audit comb publication is invalid: {slug}: {error}")
        if (not isinstance(comb_detail, dict)
                or record.get("comb_slots_match_printed")
                is not comb_detail.get("holds")):
            errors.append(f"audit top-level comb verdict is false: {slug}")
        if record.get("status") != "ok" or record.get("error") is not None:
            errors.append(f"audit status is not complete: {slug}")
        counters = (
            "rules_missing", "rules_extra", "rules_thickness_violations",
            "images_missing", "images_placement_violations",
            "text_missing", "text_extra",
        )
        if (record.get("measured") is not True
                or not isinstance(record.get("paper_ok"), bool)
                or any(not _is_count(record.get(key)) for key in counters)):
            errors.append(f"audit round-trip relation is incomplete: {slug}")
        for pct_key in _PERCENTAGE_EVIDENCE:
            errors.extend(_percentage_evidence_errors(
                record, pct_key, f"audit round-trip {slug}"))
        errors.extend(_audit_roundtrip_payload_errors(record, slug))
    if len(slugs) != len(set(slugs)):
        errors.append("audit report contains duplicate slugs")
    if set(slugs) != set(expected_slugs):
        errors.append("audit report does not match the exact tracked slug corpus")
    return errors


def _audit_runtime_projection_errors(
        runtime: dict[str, Any], snapshot_runtime: Any, slug: str,
        ) -> list[str]:
    """Bind the child-reported loaded runtime members to outer exact bytes."""
    errors: list[str] = []
    if not isinstance(snapshot_runtime, dict):
        return [f"outer audit runtime snapshot is absent: {slug}"]
    if runtime.get("python") != snapshot_runtime.get("python_identity"):
        errors.append(f"audit Python identity is not bound: {slug}")
    expected_pymupdf_version = snapshot_runtime.get(
        "pymupdf_distribution_version")
    if (not isinstance(expected_pymupdf_version, str)
            or not expected_pymupdf_version
            or runtime.get("pymupdf") != {
                "package_version": expected_pymupdf_version,
                "version_bind": expected_pymupdf_version,
            }):
        errors.append(f"audit PyMuPDF version is not bound: {slug}")
    projection = snapshot_runtime.get("python_dependency_files")
    if not isinstance(projection, dict) or set(projection) != {
            "algorithm", "files", "bytes", "members", "sha256"}:
        return [f"outer Python dependency projection is malformed: {slug}"]
    projected = projection.get("members")
    if not isinstance(projected, list):
        return [f"outer Python dependency members are malformed: {slug}"]
    projected_tuples: list[tuple[str, int, str]] = []
    for member in projected:
        if (not isinstance(member, dict)
                or set(member) != {"path", "bytes", "sha256"}
                or not isinstance(member.get("path"), str)
                or not member.get("path")
                or not _is_count(member.get("bytes"))
                or not _is_sha256(member.get("sha256"))):
            errors.append(f"outer Python dependency member is malformed: {slug}")
            continue
        projected_tuples.append(
            (member["path"], member["bytes"], member["sha256"]))
    if (projected_tuples != sorted(projected_tuples)
            or len({item[0] for item in projected_tuples})
            != len(projected_tuples)
            or projection.get("algorithm")
            != "sha256(canonical-json(path,bytes,sha256))"
            or projection.get("files") != len(projected_tuples)
            or projection.get("bytes") != sum(item[1] for item in projected_tuples)
            or projection.get("sha256") != canonical_digest([
                {"path": path, "bytes": size, "sha256": digest}
                for path, size, digest in projected_tuples
            ])):
        errors.append(f"outer Python dependency projection is false: {slug}")
    projected_by_path = {
        path: (size, digest) for path, size, digest in projected_tuples}

    loaded = runtime.get("loaded_application_files")
    loaded_members = loaded.get("members", []) if isinstance(loaded, dict) else []
    loaded_by_name = {
        member.get("file"): member
        for member in loaded_members if isinstance(member, dict)
    }
    python_record = snapshot_runtime.get("python")
    executable = loaded_by_name.get("python/executable")
    if (not isinstance(python_record, dict)
            or not isinstance(executable, dict)
            or executable.get("bytes") != python_record.get("bytes")
            or executable.get("sha256") != python_record.get("sha256")):
        errors.append(f"audit Python executable is not bound: {slug}")
    expected_library = snapshot_runtime.get("python_runtime_library")
    reported_library = loaded_by_name.get("python/runtime-library")
    if expected_library is None:
        if reported_library is not None:
            errors.append(f"audit Python runtime library is unexpected: {slug}")
    elif (not isinstance(reported_library, dict)
          or reported_library.get("bytes") != expected_library.get("bytes")
          or reported_library.get("sha256") != expected_library.get("sha256")):
        errors.append(f"audit Python runtime library is not bound: {slug}")

    module_names = [
        name for name in loaded_by_name
        if isinstance(name, str) and name.startswith("module/")]
    if "module/fitz" not in module_names or "module/pymupdf" not in module_names:
        errors.append(f"audit PyMuPDF module inventory is incomplete: {slug}")
    for logical in module_names:
        module = logical.removeprefix("module/")
        if module == "fitz":
            candidate_paths = ["fitz/__init__.py"]
        elif module == "pymupdf":
            candidate_paths = ["pymupdf/__init__.py"]
        elif re.fullmatch(
                r"pymupdf(?:\.[A-Za-z_][A-Za-z0-9_]*)+", module):
            stem = "pymupdf/" + module.removeprefix("pymupdf.").replace(".", "/")
            candidate_paths = [
                f"{stem}.py", f"{stem}/__init__.py",
                *(f"{stem}{suffix}"
                  for suffix in importlib.machinery.EXTENSION_SUFFIXES),
            ]
        else:
            candidate_paths = []
        report_member = loaded_by_name[logical]
        matches = [
            path for path in candidate_paths
            if projected_by_path.get(path) == (
                report_member.get("bytes"), report_member.get("sha256"))]
        if len(matches) != 1:
            errors.append(f"audit runtime module is not uniquely bound: {slug}/{logical}")
    unexpected = sorted(
        name for name in loaded_by_name
        if name not in {"python/executable", "python/runtime-library"}
        and name not in module_names)
    if unexpected:
        errors.append(f"audit runtime publishes unsupported members: {slug}")
    return errors


def _audit_roundtrip_snapshot_errors(
        record: dict[str, Any], snapshot_runtime: Any, slug: str,
        ) -> list[str]:
    errors: list[str] = []
    if not isinstance(snapshot_runtime, dict):
        return [f"outer roundtrip runtime snapshot is absent: {slug}"]
    runtime = record.get("roundtrip_runtime")
    if not isinstance(runtime, dict):
        return [f"audit roundtrip runtime is absent: {slug}"]
    trees_projection = snapshot_runtime.get("python_dependency_trees")
    if (not isinstance(trees_projection, dict)
            or set(trees_projection) != {"algorithm", "trees", "sha256"}
            or trees_projection.get("algorithm")
            != "per-entry-audit-tree-closure-v1"
            or not isinstance(trees_projection.get("trees"), dict)
            or trees_projection.get("sha256")
            != canonical_digest(trees_projection.get("trees", {}))):
        return [f"outer Python dependency tree projection is malformed: {slug}"]
    expected_closure = trees_projection["trees"].get("playwright")
    if (not isinstance(expected_closure, dict)
            or runtime.get("dependency_closure") != expected_closure):
        errors.append(f"audit Playwright closure is not bound: {slug}")
    expected_version = snapshot_runtime.get("playwright_distribution_version")
    if (not isinstance(expected_version, str) or not expected_version
            or runtime.get("playwright_package_version") != expected_version):
        errors.append(f"audit Playwright version is not bound: {slug}")
    files_projection = snapshot_runtime.get("python_dependency_files")
    projected: dict[str, tuple[int, str]] = {}
    if isinstance(files_projection, dict):
        members = files_projection.get("members")
        if isinstance(members, list):
            for member in members:
                if (isinstance(member, dict)
                        and isinstance(member.get("path"), str)
                        and _is_count(member.get("bytes"))
                        and _is_sha256(member.get("sha256"))):
                    projected[member["path"]] = (
                        member["bytes"], member["sha256"])
    chromium = runtime.get("chromium")
    chromium_file = chromium.get("file") if isinstance(chromium, dict) else None
    if (not isinstance(chromium_file, str)
            or projected.get(chromium_file) != (
                chromium.get("bytes"), chromium.get("sha256"))):
        errors.append(f"audit Chromium executable is not bound: {slug}")
    return errors


def _audit_payload_snapshot_binding_errors(
        data: Any, snapshot: Any,
        ) -> list[str]:
    """Cross-bind every full-audit record to the immutable outer snapshot."""
    if not isinstance(snapshot, dict):
        return ["outer audit application snapshot is not an object"]
    layout_bindings = snapshot.get("layout_bindings")
    if not isinstance(layout_bindings, dict) or not layout_bindings:
        return ["outer audit form inventory is absent"]
    expected_slugs = frozenset(layout_bindings)
    errors = full_audit_payload_errors(data, expected_slugs)
    if errors or not isinstance(data, list):
        return errors

    producers = snapshot.get("producers")
    trees = snapshot.get("artifact_trees")
    source_snapshot = snapshot.get("source_pdfs")
    snapshot_runtime = snapshot.get("runtime")
    if not isinstance(producers, dict):
        errors.append("outer audit producer snapshot is absent")
        producers = {}
    if not isinstance(trees, dict):
        errors.append("outer audit artifact trees are absent")
        trees = {}
    source_relations = (
        source_snapshot.get("relations", [])
        if isinstance(source_snapshot, dict) else [])
    if not isinstance(source_relations, list):
        return ["outer audit source relations are malformed"]
    source_by_slug = {
        relation.get("slug"): relation
        for relation in source_relations if isinstance(relation, dict)
    }
    if (len(source_by_slug) != len(source_relations)
            or set(source_by_slug) != set(expected_slugs)):
        errors.append("outer audit source relation inventory is false")

    expected_producer_files = (
        "tools/formgen/audit.py", "tools/formgen/extract.py",
        "tools/formgen/verify.py",
    )
    if any(not isinstance(trees.get(name), dict)
           for name in ("ir", "layout", "html", "guides")):
        return ["outer audit artifact tree schema is malformed"]
    tree_files = {name: _manifest_files(trees[name])
                  for name in ("ir", "layout", "html", "guides")}
    render_bindings = snapshot.get("render_bindings")
    if (not isinstance(render_bindings, dict)
            or set(render_bindings) != set(expected_slugs)):
        errors.append("outer audit render binding inventory is false")
        render_bindings = {}
    runtime_digest: str | None = None
    for record in data:
        slug = record["slug"]
        manifest = record["input_manifest"]
        producer = manifest["producer"]
        published_producers = [producer, *producer["dependencies"]]
        for relative, published in zip(
                expected_producer_files, published_producers):
            expected = producers.get(relative)
            if (not isinstance(expected, dict)
                    or published.get("file") != relative
                    or published.get("bytes") != expected.get("bytes")
                    or published.get("sha256") != expected.get("sha256")):
                errors.append(f"audit producer bytes are not bound: {slug}/{relative}")

        current_runtime_digest = canonical_digest(manifest["runtime"])
        if runtime_digest is None:
            runtime_digest = current_runtime_digest
        elif current_runtime_digest != runtime_digest:
            errors.append(f"audit base runtime differs by form: {slug}")
        errors.extend(_audit_runtime_projection_errors(
            manifest["runtime"], snapshot_runtime, slug))
        errors.extend(_audit_roundtrip_snapshot_errors(
            record, snapshot_runtime, slug))

        inputs = manifest["inputs"]
        artifacts = {
            "ir": ("ir", f"build/ir/{slug}.ir.json"),
            "layout": ("layout", f"build/layout/{slug}.layout.json"),
            "html": ("html", f"build/html/{slug}.html"),
            "guide": ("guides", f"build/guides/{slug}.guide.json"),
            "guide_html": ("html", f"build/html/{slug}.guide.html"),
        }
        for role, (tree_name, logical) in artifacts.items():
            published = inputs[role]
            expected = tree_files[tree_name].get(logical)
            if expected is None and role == "guide_html":
                if (published.get("file") != pathlib.PurePosixPath(logical).name
                        or published.get("present") is not False
                        or published.get("bytes") is not None
                        or published.get("sha256") is not None):
                    errors.append(f"audit optional input is falsely present: {slug}/{role}")
                continue
            if (not isinstance(expected, dict)
                    or published.get("present") is not True
                    or published.get("file") != pathlib.PurePosixPath(logical).name
                    or published.get("bytes") != expected.get("bytes")
                    or published.get("sha256") != expected.get("sha256")):
                errors.append(f"audit generated input is not bound: {slug}/{role}")

        source = inputs["source_pdf"]
        relation = source_by_slug.get(slug)
        selected = None
        if isinstance(relation, dict):
            selected = next((
                candidate for candidate in relation.get("candidates", [])
                if isinstance(candidate, dict)
                and candidate.get("path") == relation.get("selected")), None)
        layout_pin = relation.get("layout_pin") if isinstance(relation, dict) else None
        if (not isinstance(relation, dict)
                or not isinstance(selected, dict)
                or not isinstance(layout_pin, dict)
                or relation.get("matching_count") != 1
                or source.get("file") != relation.get("declared_file")
                or source.get("logical_identity") != layout_pin.get("file")
                or source.get("path") != relation.get("selected")
                or source.get("bytes") != relation.get("declared_bytes")
                or source.get("sha256") != relation.get("declared_sha256")
                or source.get("expected_sha256") != relation.get("declared_sha256")
                or source.get("bytes") != selected.get("bytes")
                or source.get("sha256") != selected.get("sha256")):
            errors.append(f"audit source PDF is not bound: {slug}")

        render = manifest["render"]
        render_binding = render_bindings.get(slug)
        expected_render = None
        if isinstance(render_binding, dict):
            expected_render = {
                key: value for key, value in render_binding.items()
                if key != "html_sha256"}
        if (not isinstance(render_binding, dict)
                or render_binding.get("html_sha256")
                != inputs["html"].get("sha256")
                or render != expected_render):
            errors.append(
                f"audit render dependency closure is not bound: {slug}")
        dependency_paths = {
            dependency["path"] for dependency in render["dependencies"]}
        allowed_referrers = {render["entrypoint"], *dependency_paths}
        for dependency in render["dependencies"]:
            expected = tree_files["html"].get(
                f"build/html/{dependency['path']}")
            if (not isinstance(expected, dict)
                    or dependency.get("bytes") != expected.get("bytes")
                    or dependency.get("sha256") != expected.get("sha256")
                    or not set(dependency.get("referrers", [])).issubset(
                        allowed_referrers)):
                errors.append(
                    f"audit render dependency is not bound: {slug}/{dependency['path']}")
    return errors


def audit_payload_snapshot_binding_errors(
        data: Any, snapshot: Any,
        ) -> list[str]:
    """Fail closed on malformed hostile evidence instead of propagating it."""
    try:
        return _audit_payload_snapshot_binding_errors(data, snapshot)
    except Exception as error:  # noqa: BLE001 - malformed evidence is a verdict
        return [
            "audit payload/snapshot binding is malformed: "
            f"{type(error).__name__}: {error}"]


def compose_generated_scope(
        trees: dict[str, Any], batch_report: dict[str, Any]) -> dict[str, Any]:
    expected_trees = {"forms", *COMB_REFEREE_ARTIFACT_TREES}
    if set(trees) != expected_trees:
        raise CombRefereeScopeError(
            "generated determinism scope omits or invents an artifact tree")
    unsigned = {"trees": trees, "batch_report": batch_report}
    return {**unsigned, "sha256": canonical_digest(unsigned)}


def compose_final_referee_scope(
        generation: dict[str, Any], audit_record: dict[str, Any],
        ) -> dict[str, Any]:
    unsigned = {"generation": generation, "audit": audit_record}
    return {**unsigned, "sha256": canonical_digest(unsigned)}


def current_audit_identity() -> dict[str, Any]:
    records = [
        _stable_file_record(AUDIT_JSON, "build/audit.json"),
        _stable_file_record(
            AUDIT_APPLICATION_ATTESTATION, "build/audit-attested.json"),
    ]
    return _file_manifest(records)


def generated_scope_manifest(
        batch_report: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
    """Canonical bytes consumed downstream after each batch generation."""
    trees = {
        "forms": _tree_manifest(FORMS, "forms"),
        **{
            name: _tree_manifest(path, f"build/{name}")
            for name, path in COMB_REFEREE_ARTIFACT_TREES.items()
        },
    }
    report_record = batch_report or _stable_file_record(
        BATCH_REPORT, "build/batch-report.json")
    return compose_generated_scope(trees, report_record)


def compose_audit_application_envelope(
        snapshot: dict[str, Any], payload: bytes, form_count: int,
        launcher_receipt: dict[str, Any], target_argv: Sequence[str],
        child_exit: int = 0,
        ) -> dict[str, Any]:
    try:
        audit_data = json.loads(payload)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise CombRefereeScopeError(
            f"cannot bind malformed audit payload: {error}") from error
    binding_errors = audit_payload_snapshot_binding_errors(
        audit_data, snapshot)
    if binding_errors:
        raise CombRefereeScopeError("; ".join(binding_errors[:5]))
    relations = {key: True for key in AUDIT_APPLICATION_RELATIONS}
    envelope: dict[str, Any] = {
        "schema_version": AUDIT_APPLICATION_ATTESTATION_VERSION,
        "application_scope_name": AUDIT_APPLICATION_SCOPE,
        "application_snapshot": snapshot,
        "invocation": {
            "executable": sys.executable,
            "resolved_executable": snapshot["runtime"]["python"]["path"],
            "python_flags": list(ISOLATED_PYTHON_ATTESTED_FLAGS),
            "pythonpath_removed": True,
            "pythonhome_removed": True,
            "timeout_seconds": 5400,
            "output": "private-temporary-output",
            "target_argv": list(target_argv),
            "child_exit": child_exit,
            "launcher_receipt": launcher_receipt,
        },
        "raw_report": {
            "file": "build/audit.json",
            "bytes": len(payload),
            "sha256": sha256_bytes(payload),
            "form_count": form_count,
        },
        "relations": relations,
        "host_tcb_required": True,
        "host_scope_complete": False,
        "host_closure_claimed": False,
        "operating_system_bound": False,
        "python_stdlib_bound": False,
        "dynamic_libraries_bound": False,
        "application_scope_complete": all(relations.values()),
        "enforceable": all(relations.values()),
        "enforcement_scope": "application-only",
    }
    attach_self_digest(envelope)
    return envelope


def refresh_full_audit_report(
        target: pathlib.Path = AUDIT_JSON,
        attestation_target: pathlib.Path = AUDIT_APPLICATION_ATTESTATION,
        scratch_root: pathlib.Path = BUILD,
        expected_slugs: frozenset[str] | None = None,
        scope_reader: Callable[[], dict[str, Any]] = (
            capture_audit_application_snapshot),
        ) -> Result:
    """Publish a full audit only after a successful, complete temp refresh."""
    try:
        before = scope_reader()
        scratch_root.mkdir(parents=True, exist_ok=True)
    except Exception as error:  # noqa: BLE001 - scope must fail closed
        return Result(
            "audit-refresh", Verdict.UNEVALUABLE,
            f"cannot bind audit application scope: {error}")
    with tempfile.TemporaryDirectory(
            prefix=".full-audit-", dir=scratch_root) as temporary:
        fresh = pathlib.Path(temporary) / "audit.json"
        target_argv = [str(HERE / "audit.py"), "--out", str(fresh)]
        try:
            execution = run_isolated_python_attested(
                target_argv, 5400)
            code, out = execution.code, execution.output
        except Exception as error:  # noqa: BLE001 - child failure is evidence
            return Result(
                "audit-refresh", Verdict.UNEVALUABLE,
                f"full audit refresh raised: {type(error).__name__}: {error}")
        if code != 0:
            tail = out.strip().splitlines()[-1:] or ["no diagnostic"]
            return Result("audit-refresh", Verdict.UNEVALUABLE,
                          f"full audit refresh failed: {tail[0]}")
        receipt_errors = isolated_launch_receipt_errors(
            execution.receipt, before.get("runtime", {}).get(
                "python_dependencies"), code,
            str(before.get("runtime", {}).get("python", {}).get("path", "")),
            target_argv)
        if receipt_errors:
            return Result("audit-refresh", Verdict.UNEVALUABLE,
                          "; ".join(receipt_errors[:5]))
        try:
            payload = fresh.read_bytes()
            data = json.loads(payload)
        except (OSError, UnicodeError, ValueError, RecursionError) as error:
            return Result("audit-refresh", Verdict.UNEVALUABLE,
                          f"full audit produced no usable report: {error}")
        try:
            slugs = expected_slugs or canonical_form_slugs()
        except Exception as error:  # noqa: BLE001 - corpus identity is required
            return Result("audit-refresh", Verdict.UNEVALUABLE,
                          f"cannot resolve tracked audit corpus: {error}")
        report_errors = full_audit_payload_errors(data, slugs)
        if report_errors:
            return Result("audit-refresh", Verdict.UNEVALUABLE,
                          "; ".join(report_errors[:5]))
        try:
            after = scope_reader()
            scope_errors = snapshot_pair_errors(before, after)
            if scope_errors:
                return Result(
                    "audit-refresh", Verdict.UNEVALUABLE,
                    "; ".join(scope_errors[:5]))
            envelope = compose_audit_application_envelope(
                before, payload, len(data), execution.receipt, target_argv,
                code)
            envelope_payload = (
                json.dumps(envelope, indent=2, sort_keys=True,
                           ensure_ascii=False) + "\n").encode("utf-8")
            envelope_errors = validate_audit_application_envelope(
                envelope, payload, after)
            if envelope_errors:
                return Result(
                    "audit-refresh", Verdict.UNEVALUABLE,
                    "; ".join(envelope_errors[:5]))
            _atomic_write(target, payload)
            _atomic_write(attestation_target, envelope_payload)
        except Exception as error:  # noqa: BLE001 - publication is fail closed
            return Result("audit-refresh", Verdict.UNEVALUABLE,
                          f"could not publish full audit/envelope: {error}")
    return Result("audit-refresh", Verdict.PASS,
                  f"fresh audit atomically published for {EXPECTED_FORMS} forms")


def refresh_full_pipeline(
        runner: Callable[[list[str], int], tuple[int, str]] = run,
        generation_reader: Callable[[dict[str, Any]], dict[str, Any]] = (
            generated_scope_manifest),
        audit_refresher: Callable[[], Result] = refresh_full_audit_report,
        referee_refresher: Callable[[], Result] | None = (
            refresh_comb_referee_report),
        scratch_root: pathlib.Path = BUILD,
        batch_target: pathlib.Path = BATCH_REPORT,
        expected_slugs: frozenset[str] | None = None,
        expected_inventory: dict[str, bool] | None = None,
        audit_identity_reader: Callable[[], dict[str, Any]] = (
            current_audit_identity),
        ) -> FullRefresh:
    """Two generations first, audit the final bytes, then referee exactly last."""
    diagnostics: list[str] = []
    try:
        if expected_slugs is None and expected_inventory is None:
            inventory = canonical_form_inventory()
            slugs = frozenset(inventory)
        elif (expected_slugs is not None
              and isinstance(expected_inventory, dict)
              and set(expected_inventory) == set(expected_slugs)
              and all(isinstance(value, bool)
                      for value in expected_inventory.values())):
            slugs = expected_slugs
            inventory = dict(expected_inventory)
        else:
            raise CombRefereeScopeError(
                "custom generation slugs require an exact root inventory")
        scratch_root.mkdir(parents=True, exist_ok=True)
    except Exception as error:  # noqa: BLE001 - corpus identity is required
        return FullRefresh(
            Result("determinism", Verdict.UNEVALUABLE,
                   f"cannot resolve exact generation corpus: {error}"),
            Result("audit-refresh", Verdict.UNEVALUABLE,
                   "generation corpus is unknown; audit not run"),
            Result("comb-referee", Verdict.UNEVALUABLE,
                   "generation corpus is unknown; referee not run"),
            diagnostics, None,
        )
    generations: list[dict[str, Any]] = []
    batch_payloads: list[bytes] = []
    with tempfile.TemporaryDirectory(
            prefix=".gate-batches-", dir=scratch_root) as temporary:
        for run_index in range(2):
            fresh_report = pathlib.Path(temporary) / (
                f"batch-{run_index + 1}.json")
            batch_args = [
                str(HERE / "batch.py"), "--report", str(fresh_report)]
            try:
                code, out = runner(batch_args, 5400)
            except Exception as error:  # noqa: BLE001
                code, out = 1, f"{type(error).__name__}: {error}"
            if code != 0:
                diagnostics.append(
                    f"batch #{run_index + 1} failed:\n{out[-2000:]}")
                return FullRefresh(
                    Result("determinism", Verdict.FAIL,
                           f"regenerate #{run_index + 1} failed"),
                    Result("audit-refresh", Verdict.UNEVALUABLE,
                           "final generation failed; audit not run"),
                    Result("comb-referee", Verdict.UNEVALUABLE,
                           "final corpus was not generated; referee not run"),
                    diagnostics, None,
                )
            try:
                report_record, payload = _fresh_batch_report(
                    fresh_report, slugs, inventory)
                generation = generation_reader(report_record)
            except Exception as error:  # noqa: BLE001
                return FullRefresh(
                    Result("determinism", Verdict.UNEVALUABLE,
                           f"cannot attest generation #{run_index + 1}: {error}"),
                    Result("audit-refresh", Verdict.UNEVALUABLE,
                           "generation attestation failed; audit not run"),
                    Result("comb-referee", Verdict.UNEVALUABLE,
                           "generation attestation failed; referee not run"),
                    diagnostics, None,
                )
            generations.append(generation)
            batch_payloads.append(payload)

    first_generation, second_generation = generations
    first_digest = first_generation.get("sha256")
    second_digest = second_generation.get("sha256")
    if (not _is_sha256(first_digest)
            or first_generation != second_generation):
        determinism = Result(
            "determinism", Verdict.FAIL,
            "generated forms/build/batch evidence differs between runs "
            f"({str(first_digest)[:12]} vs {str(second_digest)[:12]})")
        return FullRefresh(
            determinism,
            Result("audit-refresh", Verdict.UNEVALUABLE,
                   "nondeterministic generation; audit not run"),
            Result("comb-referee", Verdict.UNEVALUABLE,
                   "nondeterministic generation; referee not run"),
            diagnostics, None,
        )
    determinism = Result(
        "determinism", Verdict.PASS,
        f"byte-identical ({first_digest[:12]})")
    try:
        _atomic_write(batch_target, batch_payloads[1])
        canonical_batch, _payload = _fresh_batch_report(
            batch_target, slugs, inventory)
        published_generation = generation_reader(canonical_batch)
    except Exception as error:  # noqa: BLE001
        return FullRefresh(
            Result("determinism", Verdict.UNEVALUABLE,
                   f"could not publish/bind final batch report: {error}"),
            Result("audit-refresh", Verdict.UNEVALUABLE,
                   "final batch report is not bound; audit not run"),
            Result("comb-referee", Verdict.UNEVALUABLE,
                   "final batch report is not bound; referee not run"),
            diagnostics, None,
        )
    if published_generation != second_generation:
        return FullRefresh(
            Result("determinism", Verdict.FAIL,
                   "published batch report changed deterministic scope"),
            Result("audit-refresh", Verdict.UNEVALUABLE,
                   "published scope mismatch; audit not run"),
            Result("comb-referee", Verdict.UNEVALUABLE,
                   "published scope mismatch; referee not run"),
            diagnostics, None,
        )

    try:
        audit_refresh = audit_refresher()
    except Exception as error:  # noqa: BLE001 - failed child is not evidence
        audit_refresh = Result(
            "audit-refresh", Verdict.UNEVALUABLE,
            f"full audit refresh raised: {type(error).__name__}: {error}")
    if not audit_refresh.verdict.ok:
        return FullRefresh(
            determinism,
            audit_refresh,
            Result("comb-referee", Verdict.UNEVALUABLE,
                   "fresh final-corpus audit failed; referee not run"),
            diagnostics, None,
        )
    try:
        post_audit_generation = generation_reader(canonical_batch)
    except Exception as error:  # noqa: BLE001
        return FullRefresh(
            Result("determinism", Verdict.UNEVALUABLE,
                   f"cannot revalidate generated scope after audit: {error}"),
            Result("audit-refresh", Verdict.UNEVALUABLE,
                   "post-audit generated scope could not be attested"),
            Result("comb-referee", Verdict.UNEVALUABLE,
                   "post-audit scope is unknown; referee not run"),
            diagnostics, None,
        )
    if post_audit_generation != second_generation:
        return FullRefresh(
            Result("determinism", Verdict.FAIL,
                   "generated scope changed during final audit"),
            Result("audit-refresh", Verdict.UNEVALUABLE,
                   "audit mutated deterministic generated bytes"),
            Result("comb-referee", Verdict.UNEVALUABLE,
                   "audit mutated generated bytes; referee not run"),
            diagnostics, None,
        )
    try:
        final_scope = compose_final_referee_scope(
            second_generation, audit_identity_reader())
    except Exception as error:  # noqa: BLE001
        return FullRefresh(
            determinism,
            Result("audit-refresh", Verdict.UNEVALUABLE,
                   f"cannot bind final audit bytes: {error}"),
            Result("comb-referee", Verdict.UNEVALUABLE,
                   "final audit bytes are unbound; referee not run"),
            diagnostics, None,
        )
    if referee_refresher is None:
        return FullRefresh(
            determinism, audit_refresh,
            Result("comb-referee", Verdict.UNEVALUABLE,
                   "referee deferred until all other gate checks finish"),
            diagnostics, final_scope,
        )
    try:
        comb_referee = referee_refresher()
    except Exception as error:  # noqa: BLE001 - failed child is not evidence
        comb_referee = Result(
            "comb-referee", Verdict.UNEVALUABLE,
            f"referee refresh raised: {type(error).__name__}: {error}")
    return FullRefresh(
        determinism, audit_refresh, comb_referee, diagnostics,
        final_scope)


def refresh_final_comb_referee(
        expected_scope: dict[str, Any] | None,
        *, referee_refresher: Callable[[], Result] = (
            refresh_comb_referee_report),
        generation_reader: Callable[[dict[str, Any]], dict[str, Any]] = (
            generated_scope_manifest),
        batch_target: pathlib.Path = BATCH_REPORT,
        expected_slugs: frozenset[str] | None = None,
        expected_inventory: dict[str, bool] | None = None,
        audit_identity_reader: Callable[[], dict[str, Any]] = (
            current_audit_identity),
        ) -> Result:
    """Last executable gate step: rebind current bytes, then run the referee."""
    if expected_scope is None:
        return Result(
            "comb-referee", Verdict.UNEVALUABLE,
            "no deterministic post-audit scope exists; referee not run",
        )
    try:
        if expected_slugs is None and expected_inventory is None:
            inventory = canonical_form_inventory()
            slugs = frozenset(inventory)
        elif (expected_slugs is not None
              and isinstance(expected_inventory, dict)
              and set(expected_inventory) == set(expected_slugs)
              and all(isinstance(value, bool)
                      for value in expected_inventory.values())):
            slugs = expected_slugs
            inventory = dict(expected_inventory)
        else:
            raise CombRefereeScopeError(
                "custom generation slugs require an exact root inventory")
        batch_record, _payload = _fresh_batch_report(
            batch_target, slugs, inventory)
        current_generation = generation_reader(batch_record)
        current_scope = compose_final_referee_scope(
            current_generation, audit_identity_reader())
    except Exception as error:  # noqa: BLE001 - currentness is mandatory
        return Result(
            "comb-referee", Verdict.UNEVALUABLE,
            f"cannot revalidate final generated scope: {error}",
        )
    if current_scope != expected_scope:
        return Result(
            "comb-referee", Verdict.UNEVALUABLE,
            "generated scope changed after audit/other checks; referee not run",
        )
    try:
        return referee_refresher()
    except Exception as error:  # noqa: BLE001
        return Result(
            "comb-referee", Verdict.UNEVALUABLE,
            f"referee refresh raised: {type(error).__name__}: {error}",
        )


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------


def check_self_tests() -> Result:
    failures, missing = [], []
    for module in SELF_TEST_MODULES:
        path = HERE / f"{module}.py"
        if not path.is_file():
            missing.append(module)
            continue
        args = [str(path), "--self-test"]
        # Two modules key their self-test off a form's IR rather than shipping a
        # fixture, so the gate supplies one instead of skipping them.
        if module in ("lattice", "fonts"):
            ir = BUILD / "ir" / "2551q-2018.ir.json"
            if not ir.is_file():
                missing.append(f"{module} (no IR to test against)")
                continue
            args += ["--ir", str(ir)]
        if module in SELF_SUPERVISING_SELF_TEST_MODULES:
            runner = run_self_supervising_python
        elif module == "audit":
            runner = run_dependency_self_test_python
        else:
            runner = run
        code, _ = runner(args, timeout=900)
        if code != 0:
            failures.append(module)
    forms_tree = REPO / "forms"
    if EXPECTED_UNCATALOGUED_FILLABLES != 0:
        failures.append("EXPECTED_UNCATALOGUED_FILLABLES must stay 0")
    if forms_tree.is_dir():
        code, _ = run(
            [str(HERE / "field_identity.py"), "coverage", "--tree", str(forms_tree)],
            timeout=120)
        if code != 0:
            failures.append("field_identity coverage")
    prover = HERE / PROVE_FIXTURES_SCRIPT
    if not prover.is_file():
        missing.append(PROVE_FIXTURES_SCRIPT)
    else:
        # It rebuilds and re-pins the fixture corpus once per mutation, so it is
        # slower than any single module's self-test and gets its own budget.
        code, _ = run([str(prover)], timeout=1800)
        if code != 0:
            failures.append("prove-fixtures-fail")
    if missing:
        return Result("self-tests", Verdict.UNEVALUABLE,
                      f"cannot run: {', '.join(missing)}")
    if failures:
        return Result("self-tests", Verdict.FAIL, f"failing: {', '.join(failures)}")
    return Result("self-tests", Verdict.PASS,
                  f"{len(SELF_TEST_MODULES)} modules pass, mutations proven")


def check_conversion() -> Result:
    report = load(BATCH_REPORT)
    try:
        inventory = canonical_form_inventory()
        expected_slugs = frozenset(inventory)
    except Exception as error:  # noqa: BLE001 - exact corpus is mandatory
        return Result("conversion", Verdict.UNEVALUABLE,
                      f"cannot resolve tracked corpus: {error}")
    errors = batch_report_errors(report, expected_slugs, inventory)
    if errors:
        return Result("conversion", Verdict.FAIL, "; ".join(errors[:5]))
    return Result(
        "conversion", Verdict.PASS,
        f"{len(expected_slugs)}/{EXPECTED_FORMS} unique tracked forms converted",
    )


def _audit_corpus_records() -> tuple[list[dict[str, Any]], list[str]]:
    """Load the exact audit corpus without hiding malformed/error records."""
    data = load(AUDIT_JSON)
    if not isinstance(data, list):
        return [], ["audit report is absent or not a list"]
    errors: list[str] = []
    records: list[dict[str, Any]] = []
    slugs: list[str] = []
    for index, value in enumerate(data):
        if not isinstance(value, dict):
            errors.append(f"audit record {index} is not an object")
            continue
        records.append(value)
        slug = value.get("slug")
        if not isinstance(slug, str) or not slug:
            errors.append(f"audit record {index} has no slug")
        else:
            slugs.append(slug)
        if value.get("status") not in {"ok", "error"}:
            errors.append(f"audit record {slug or index} has invalid status")
    if len(data) != EXPECTED_FORMS:
        errors.append(f"audit covers {len(data)}/{EXPECTED_FORMS} forms")
    if len(slugs) != len(set(slugs)):
        errors.append("audit report contains duplicate slugs")
    try:
        expected_slugs = canonical_form_slugs()
    except Exception as error:  # noqa: BLE001 - corpus identity is evidence
        errors.append(f"cannot resolve tracked audit corpus: {error}")
    else:
        if set(slugs) != set(expected_slugs):
            errors.append("audit report does not match the exact tracked slug corpus")
    return records, errors


def _metric_record_errors(
        records: Sequence[dict[str, Any]], keys: Iterable[str] = (),
        pct_key: str | None = None, paper: bool = False,
        ) -> list[str]:
    """Validate evidence shape before interpreting any metric value."""
    errors: list[str] = []
    required_keys = tuple(keys)
    for index, record in enumerate(records):
        slug = record.get("slug")
        label = slug if isinstance(slug, str) and slug else str(index)
        if record.get("status") != "ok":
            errors.append(f"{label}: audit status is not ok")
        if record.get("measured") is not True:
            errors.append(f"{label}: measured is not exactly true")
        for key in required_keys:
            if not _is_count(record.get(key)):
                errors.append(f"{label}: {key} is absent or not a nonnegative int")
        if pct_key is not None:
            errors.extend(_percentage_evidence_errors(
                record, pct_key, label))
        if paper and not isinstance(record.get("paper_ok"), bool):
            errors.append(f"{label}: paper_ok is absent or not boolean")
    return errors


def _metric_audit_records(
        keys: Iterable[str] = (), pct_key: str | None = None,
        paper: bool = False,
        ) -> tuple[list[dict[str, Any]], list[str]]:
    records, errors = _audit_corpus_records()
    if not errors:
        errors.extend(_metric_record_errors(records, keys, pct_key, paper))
    return records, errors


def assertion_payload_errors(
        data: Any, expected_slugs: frozenset[str],
        ) -> list[str]:
    """Validate the exact assertion-only publication before use or publish."""
    if not isinstance(data, list):
        return ["assertion audit report is not a list"]
    errors: list[str] = []
    slugs: list[str] = []
    for index, record in enumerate(data):
        if not isinstance(record, dict):
            errors.append(f"assertion audit record is not an object: {index}")
            continue
        slug = record.get("slug")
        if not isinstance(slug, str) or not slug:
            errors.append(f"assertion audit record has no slug: {index}")
            continue
        slugs.append(slug)
        if record.get("status") != "ok" or record.get("error") is not None:
            errors.append(f"assertion audit record is not complete: {slug}")
        details = record.get("assertions")
        if (not isinstance(details, dict)
                or set(details) != set(REQUIRED_ASSERTIONS)):
            errors.append(f"assertion inventory is malformed: {slug}")
            continue
        held = 0
        for key in REQUIRED_ASSERTIONS:
            detail = details.get(key)
            top_level = record.get(key)
            if (not isinstance(top_level, bool)
                    or not isinstance(detail, dict)
                    or not isinstance(detail.get("holds"), bool)
                    or top_level is not detail["holds"]):
                errors.append(f"assertion relation is false: {slug}/{key}")
                continue
            if key == "comb_slots_match_printed":
                try:
                    _normalise_outer_comb_assertion(detail)
                except CombRefereeScopeError as error:
                    errors.append(
                        f"comb assertion publication is invalid: {slug}: {error}")
            else:
                errors.extend(
                    f"assertion publication is invalid: {slug}: {item}"
                    for item in _basic_assertion_detail_errors(key, detail))
            if top_level:
                held += 1
        if record.get("assertions_held") != held:
            errors.append(f"assertion total is false: {slug}")
    if len(slugs) != len(set(slugs)):
        errors.append("assertion audit contains duplicate slugs")
    if set(slugs) != set(expected_slugs):
        errors.append("assertion audit does not match the exact tracked slug corpus")
    return errors


def refresh_assertions_report(
    target: pathlib.Path = AUDIT_JSON,
    scratch_root: pathlib.Path = BUILD,
    runner: Callable[[list[str], int], tuple[int, str]] = run,
    expected_slugs: frozenset[str] | None = None,
) -> Result | None:
    """Atomically refresh the assertion audit, or return a fail-closed result."""
    scratch_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".assertions-", dir=scratch_root) as tmp:
        fresh = pathlib.Path(tmp) / "audit.json"
        code, out = runner(
            [str(HERE / "audit.py"), "--assertions-only", "--out", str(fresh)],
            5400,
        )
        if code != 0:
            tail = out.strip().splitlines()[-1:] or ["no diagnostic"]
            return Result("assertions", Verdict.UNEVALUABLE,
                          f"assertion audit refresh failed: {tail[0]}")
        data = load(fresh)
        try:
            slugs = expected_slugs or canonical_form_slugs()
        except Exception as error:  # noqa: BLE001 - corpus is evidence
            return Result("assertions", Verdict.UNEVALUABLE,
                          f"cannot resolve tracked assertion corpus: {error}")
        report_errors = assertion_payload_errors(data, slugs)
        if report_errors:
            return Result("assertions", Verdict.UNEVALUABLE,
                          "; ".join(report_errors[:5]))
        try:
            fresh.replace(target)
        except OSError as error:
            return Result("assertions", Verdict.UNEVALUABLE,
                          f"could not publish refreshed assertion audit: {error}")
    return None


def _tally(name: str, keys: Iterable[str], pct_key: str | None = None) -> Result:
    key_tuple = tuple(keys)
    records, errors = _metric_audit_records(key_tuple, pct_key)
    if errors:
        return Result(name, Verdict.UNEVALUABLE, "; ".join(errors[:5]))

    bad: list[str] = []
    for key in key_tuple:
        offenders = [r for r in records if r[key] != 0]
        if offenders:
            total = sum(r[key] for r in offenders)
            bad.append(f"{key}={total} on {len(offenders)} form(s) "
                       f"(e.g. {offenders[0]['slug']})")
    if pct_key:
        short = [r for r in records if r[pct_key] != 100.0]
        if short:
            worst = min(short, key=lambda r: r[pct_key])
            bad.append(f"{pct_key} below 100 on {len(short)} form(s), "
                       f"worst {worst['slug']} {worst[pct_key]}%")
    if bad:
        return Result(name, Verdict.FAIL, "; ".join(bad))
    return Result(name, Verdict.PASS, f"clean on {len(records)}/{EXPECTED_FORMS}")


def check_rules() -> Result:
    return _tally("rules", ("rules_missing", "rules_extra",
                            "rules_thickness_violations"), "rules_pct")


def check_paper() -> Result:
    records, errors = _metric_audit_records(paper=True)
    if errors:
        return Result("paper", Verdict.UNEVALUABLE, "; ".join(errors[:5]))
    bad = [r["slug"] for r in records if r["paper_ok"] is False]
    if bad:
        return Result("paper", Verdict.FAIL, f"{len(bad)} form(s): {', '.join(bad[:5])}")
    return Result("paper", Verdict.PASS, f"exact on {len(records)}/{EXPECTED_FORMS}")


def check_artwork() -> Result:
    return _tally("artwork", ("images_missing", "images_placement_violations"))


def check_text() -> Result:
    return _tally("text", ("text_missing", "text_extra"), "text_pct")


def check_assertions() -> Result:
    """The eight assertions that would have caught the 137 audit-blind defects.

    audit.py owns them. Until each publishes a boolean per form, this reports
    UNEVALUABLE, which the gate counts as a failure -- the whole point of the
    exercise is that an unchecked claim must not read as a satisfied one.
    """
    records, errors = _audit_corpus_records()
    if errors:
        return Result("assertions", Verdict.UNEVALUABLE, "; ".join(errors[:5]))
    try:
        slugs = canonical_form_slugs()
    except Exception as error:  # noqa: BLE001 - corpus identity is evidence
        return Result(
            "assertions", Verdict.UNEVALUABLE,
            f"cannot resolve tracked assertion corpus: {error}")
    publication_errors = assertion_payload_errors(records, slugs)
    if publication_errors:
        return Result(
            "assertions", Verdict.UNEVALUABLE,
            "; ".join(publication_errors[:5]))
    violations: list[str] = []
    for key, description in REQUIRED_ASSERTIONS.items():
        offenders = [r["slug"] for r in records if r[key] is False]
        if offenders:
            violations.append(f"{key} fails on {len(offenders)} form(s) "
                              f"({description})")
    if violations:
        return Result("assertions", Verdict.FAIL, "; ".join(violations))
    return Result("assertions", Verdict.PASS,
                  f"all {len(REQUIRED_ASSERTIONS)} hold on {len(records)} forms")


FINDINGS_TOP_LEVEL_KEYS = {
    "schema_version", "source", "note", "cause_codes", "findings",
}
FINDING_KEYS = {
    "id", "form", "page", "severity", "status", "what", "where",
    "evidence", "cause", "audit_blind", "resolution",
}
FINDING_SEVERITIES = {"blocker", "major", "minor", "cosmetic"}
FINDING_STATUSES = {"open", "fixed", "not-a-defect"}
FINDINGS_BASELINE_COUNT = 138
FINDING_IMMUTABLE_KEYS = (
    "id", "form", "page", "severity", "what", "where", "evidence",
    "cause", "audit_blind",
)
# Recited 2026-08-18: dead pXcN in where/what of the first 138 entries
# became catalog ids, live non-fillable cell ids, or former_pXcN. The
# visual claims in what/evidence are otherwise the same review. Moving
# this pin without a matching ledger recitation is still a rewrite.
FINDINGS_IMMUTABLE_BASELINE_SHA256 = (
    "5078408e0b8efd59e57071648a9918292bdc70998bb39fe17b4a010321c88d71"
)


def findings_payload_errors(data: Any) -> list[str]:
    """Validate the append-only review ledger before interpreting status."""
    if not isinstance(data, dict) or set(data) != FINDINGS_TOP_LEVEL_KEYS:
        return ["findings ledger schema is unsupported"]
    errors: list[str] = []
    cause_codes = data.get("cause_codes")
    if (data.get("schema_version") != 1
            or not isinstance(data.get("source"), str)
            or not data.get("source")
            or not isinstance(data.get("note"), str)
            or not data.get("note")
            or not isinstance(cause_codes, dict)
            or not all(isinstance(key, str) and key
                       and isinstance(value, str) and value
                       for key, value in (
                           cause_codes.items()
                           if isinstance(cause_codes, dict) else ()))):
        errors.append("findings ledger metadata is malformed")
    findings = data.get("findings")
    if not isinstance(findings, list):
        return [*errors, "findings inventory is not a list"]
    if len(findings) < FINDINGS_BASELINE_COUNT:
        errors.append(
            f"findings ledger has {len(findings)}/{FINDINGS_BASELINE_COUNT} "
            "immutable historical entries")
    identifiers: list[str] = []
    valid_causes = set(cause_codes) if isinstance(cause_codes, dict) else set()
    for index, finding in enumerate(findings, 1):
        if not isinstance(finding, dict) or set(finding) != FINDING_KEYS:
            errors.append(f"finding {index} schema is unsupported")
            continue
        identifier = finding.get("id")
        identifiers.append(identifier if isinstance(identifier, str) else "")
        resolution = finding.get("resolution")
        cause = finding.get("cause")
        if (identifier != f"F{index:03d}"
                or not isinstance(finding.get("form"), str)
                or not finding.get("form")
                or not _is_count(finding.get("page"))
                or finding.get("severity") not in FINDING_SEVERITIES
                or finding.get("status") not in FINDING_STATUSES
                or any(not isinstance(finding.get(key), str)
                       or not finding.get(key)
                       for key in ("what", "where", "evidence"))
                or (cause is not None and cause not in valid_causes)
                or not isinstance(finding.get("audit_blind"), bool)
                or (resolution is not None
                    and not isinstance(resolution, str))):
            errors.append(f"finding {identifier or index} is malformed")
        if (finding.get("status") in {"fixed", "not-a-defect"}
                and (not isinstance(resolution, str)
                     or not resolution.strip())):
            errors.append(
                f"finding {identifier or index} has no resolution evidence")
    if len(identifiers) != len(set(identifiers)):
        errors.append("findings ledger contains duplicate identifiers")
    if len(findings) >= FINDINGS_BASELINE_COUNT:
        try:
            immutable_projection = {
                "cause_codes": cause_codes,
                "findings": [
                    {key: finding[key] for key in FINDING_IMMUTABLE_KEYS}
                    for finding in findings[:FINDINGS_BASELINE_COUNT]
                ],
            }
            immutable_digest = canonical_digest(immutable_projection)
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            errors.append(
                "findings immutable baseline is unevaluable: "
                f"{type(error).__name__}: {error}")
        else:
            if immutable_digest != FINDINGS_IMMUTABLE_BASELINE_SHA256:
                errors.append("findings immutable baseline was rewritten")
    return errors


def check_findings() -> Result:
    data = load(FINDINGS)
    errors = findings_payload_errors(data)
    if errors:
        return Result("findings", Verdict.UNEVALUABLE, "; ".join(errors[:5]))
    findings = data["findings"]
    gating = [f for f in findings if f["severity"] in ("blocker", "major")]
    unresolved = [f for f in gating
                  if f["status"] not in ("fixed", "not-a-defect")
                  or not (f["resolution"] or "").strip()]
    if unresolved:
        by_form: dict[str, int] = {}
        for f in unresolved:
            by_form[f["form"]] = by_form.get(f["form"], 0) + 1
        worst = sorted(by_form.items(), key=lambda kv: -kv[1])[:4]
        return Result("findings", Verdict.FAIL,
                      f"{len(unresolved)}/{len(gating)} blocker+major unresolved "
                      f"(worst: {', '.join(f'{k} {v}' for k, v in worst)})")
    return Result("findings", Verdict.PASS,
                  f"all {len(gating)} blocker+major resolved")


def check_determinism(regenerate: bool) -> Result:
    del regenerate
    return Result(
        "determinism", Verdict.UNEVALUABLE,
        "needs the full two-generation pipeline; --only/--skip cannot evaluate it",
    )


def _tracked_deletions_from_porcelain(porcelain: str) -> list[str]:
    deleted = []
    for line in porcelain.splitlines():
        if len(line) < 3:
            continue
        index_status, worktree_status = line[0], line[1]
        if index_status == "D" or worktree_status == "D":
            deleted.append(line[3:])
    return deleted


def _tracked_deletion_result(porcelain: str) -> Result:
    deleted = _tracked_deletions_from_porcelain(porcelain)
    if deleted:
        return Result(
            "tracked-files", Verdict.FAIL,
            f"{len(deleted)} tracked file(s) deleted: "
            f"{', '.join(deleted[:3])}")
    return Result("tracked-files", Verdict.PASS, "no tracked deletion")


def check_no_tracked_deletions() -> Result:
    proc = subprocess.run(["git", "status", "--porcelain", "--", "forms/"],
                          cwd=REPO, capture_output=True, text=True)
    if proc.returncode != 0:
        return Result("tracked-files", Verdict.UNEVALUABLE, "git status failed")
    return _tracked_deletion_result(proc.stdout)


def corrected_tree_result(*, tree_exists: bool, manifest_exists: bool,
                          verify_code: int | None, verify_output: str,
                          divergence_reports: list[str],
                          fidelity_text: str | None,
                          ledger_nonempty: bool = False) -> Result:
    """ARCHITECTURE.md rule 4, in the half that is checkable today.

    The rule: "The gate runs on BOTH trees. On forms-corrected/, fidelity must
    fail ONLY at the declared divergences, each named per rule 1; an undeclared
    diff between the trees is a build failure, not a shrug."

    Pure, so the fixtures in `self_test` drive every branch without a
    filesystem or a subprocess. What it does NOT do is as important as what it
    does, so it is stated rather than implied:

      * **No corrected tree and an EMPTY ledger -> PASS.** Stage 2 is unbuilt.
        That is a true statement about a tree that does not exist, not a check
        that was skipped, and it is the one branch where absence is an answer
        -- because nothing downstream reads a tree that is not there.
      * **No corrected tree and a NON-EMPTY ledger -> FAIL.** The moment a
        correction record exists, absence stops being an answer: a declared
        override that nothing has applied is a divergence nothing publishes,
        which is rule 1's silent override reached by not building rather than
        by hiding. The check does NOT apply the ledger itself to find out --
        applying one record while its siblings are mid-write would refuse the
        whole tree and report a ledger problem as a build problem. It only
        needs to know that a tree is owed.
      * **A corrected tree with no manifest -> FAIL.** Bytes nobody can
        re-derive from a named batch are exactly the parallel corpus rule 2
        forbids.
      * **A manifest `correct.py verify` cannot re-derive -> FAIL.** That
        command is the independent re-derivation: it rebuilds copy(batch) +
        records from the batch and the ledger and treats the tree on disk as a
        suspect. This check does not re-implement it; re-implementing it here
        would be a second opinion that shares this file's assumptions.
      * **A verified manifest declaring NO divergence -> PASS.**
      * **A declared divergence with no fidelity report naming it -> FAIL.**
        This is the branch that must never become a pass. A correction whose
        divergence nothing publishes is precisely the silent override rule 1
        exists to forbid, and `correct.build_manifest` generates the exact
        sentence a report has to print so a downstream reporter cannot
        paraphrase it into a green tick. There is no fidelity report over
        forms-corrected/ yet, so today this branch means "declare a correction
        and the gate goes red until the report exists" -- the fail-closed
        direction, and the reason this check can land before that report does.
    """
    if not tree_exists:
        if ledger_nonempty:
            return Result(
                "corrected-tree", Verdict.FAIL,
                "stage 2 ledger has records but forms-corrected/ does not "
                "exist, so a declared override is applied to nothing and "
                "published by nothing; build the tree and write "
                f"{CORRECTED_FIDELITY_REPORT.name}")
        return Result("corrected-tree", Verdict.PASS,
                      "forms-corrected/ does not exist; stage 2 is unbuilt")
    if not manifest_exists:
        return Result("corrected-tree", Verdict.FAIL,
                      "forms-corrected/ exists with no manifest, so nothing "
                      "says which batch it came from or what was applied")
    if verify_code != 0:
        tail = " ".join((verify_output or "").split())[-160:]
        return Result("corrected-tree", Verdict.FAIL,
                      "correct.py verify does not re-derive the corrected "
                      f"tree: {tail or 'no output'}")
    if not divergence_reports:
        return Result("corrected-tree", Verdict.PASS,
                      "manifest verifies; no divergence declared")
    if fidelity_text is None:
        return Result(
            "corrected-tree", Verdict.FAIL,
            f"{len(divergence_reports)} declared divergence(s) and no fidelity "
            f"report at {CORRECTED_FIDELITY_REPORT.name} to publish them")
    missing = [report for report in divergence_reports
               if report not in fidelity_text]
    if missing:
        return Result(
            "corrected-tree", Verdict.FAIL,
            f"{len(missing)}/{len(divergence_reports)} declared divergence(s) "
            f"absent from the fidelity report: {'; '.join(missing[:2])[:140]}")
    return Result(
        "corrected-tree", Verdict.PASS,
        f"manifest verifies; all {len(divergence_reports)} declared "
        f"divergence(s) named in the fidelity report")


def ledger_record_names() -> list[str]:
    """Correction records in the ledger ROOT, by file name.

    `correct.load_records` reads `*.json` at the root and nothing below it, and
    refuses a nested file that validates as a record. That rule is restated
    rather than imported so this check does not inherit the applier's
    assumptions about its own ledger; the names are all that is needed, and a
    ledger directory that is missing entirely reads as empty here because
    `correct.py` is the thing that refuses on that -- not the gate's business
    to decide twice, and the branch it feeds is the conservative one either
    way.
    """
    if not CORRECTIONS_LEDGER.is_dir():
        return []
    return sorted(path.name for path in CORRECTIONS_LEDGER.iterdir()
                  if path.is_file() and path.suffix == ".json"
                  and not path.name.startswith("."))


def check_corrected_tree() -> Result:
    tree_exists = CORRECTED_TREE.is_dir()
    manifest_exists = CORRECTED_MANIFEST.is_file()
    records = ledger_record_names()
    if not tree_exists or not manifest_exists:
        result = corrected_tree_result(
            tree_exists=tree_exists, manifest_exists=manifest_exists,
            verify_code=None, verify_output="", divergence_reports=[],
            fidelity_text=None, ledger_nonempty=bool(records))
        if not tree_exists and records:
            result.detail += f" -- {len(records)} record(s): {', '.join(records)}"
        return result
    code, output = run([str(HERE / "correct.py"), "--verify",
                        "--manifest", str(CORRECTED_MANIFEST)])
    reports: list[str] = []
    try:
        manifest = json.loads(CORRECTED_MANIFEST.read_text(encoding="utf-8"))
        declared = manifest.get("divergences")
        if isinstance(declared, list):
            reports = [str(entry.get("report")) for entry in declared
                       if isinstance(entry, dict)]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        # An unreadable manifest is `correct.py verify`'s failure to report,
        # not this function's to guess at. If verify somehow passed on it, an
        # empty divergence list is not treated as "no divergence": the verify
        # branch above has already decided, and a manifest this file cannot
        # parse is named here so the two can never disagree silently.
        return Result("corrected-tree", Verdict.FAIL,
                      "the corrected tree's manifest cannot be read as JSON, "
                      "so its declared divergences cannot be checked")
    return corrected_tree_result(
        tree_exists=True, manifest_exists=True, verify_code=code,
        verify_output=output, divergence_reports=reports,
        fidelity_text=fidelity_report_text(), ledger_nonempty=bool(records))


def _json_strings(value: Any, out: list[str]) -> None:
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for key, child in value.items():
            out.append(str(key))
            _json_strings(child, out)
    elif isinstance(value, list):
        for child in value:
            _json_strings(child, out)


def fidelity_report_text() -> str | None:
    """The corrected tree's fidelity report, as text a sentence can be found in.

    The report's FORMAT is not fixed yet -- it is the half of rule 4 that does
    not exist -- so this reads it both ways and never depends on the choice. If
    it parses as JSON, every string it contains is decoded and joined, because
    the divergence sentence `correct.build_manifest` generates contains double
    quotes and would not survive a raw substring search against the escaped
    bytes. That was not a hypothetical: the first end-to-end run of this check
    reported a sentence as absent from a report that plainly contained it. If
    it is not JSON, the raw text is used unchanged.
    """
    if not CORRECTED_FIDELITY_REPORT.is_file():
        return None
    try:
        raw = CORRECTED_FIDELITY_REPORT.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # Unreadable is not absent, and it is certainly not "published".
        # Returning empty text keeps the caller on the FAIL branch that names
        # the missing sentences rather than the one that names a missing file.
        return ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    strings: list[str] = []
    _json_strings(parsed, strings)
    return "\n".join(strings)


CHECKS: dict[str, Callable[[], Result]] = {
    "self-tests": check_self_tests,
    "conversion": check_conversion,
    "rules": check_rules,
    "paper": check_paper,
    "artwork": check_artwork,
    "text": check_text,
    "assertions": check_assertions,
    "comb-referee": check_comb_referee,
    "findings": check_findings,
    "tracked-files": check_no_tracked_deletions,
    "corrected-tree": check_corrected_tree,
}


def _resign_for_self_test(value: dict[str, Any]) -> None:
    value.pop("payload_sha256", None)
    value.pop("self_digest", None)
    attach_self_digest(value)


def _producer_raw_referee_attestation_fixture() -> dict[str, Any]:
    return {
        "schema": "comb-referee-runtime-attestation-v1",
        "producer_and_declared_dependency_bytes_bound": True,
        "published_form_input_bytes_bound_before_after": True,
        "python_executable_fingerprinted": True,
        "python_executable_validated_before_after": False,
        "poppler_executable_bound_before_after": True,
        "poppler_invocations_have_hard_deadlines": True,
        "poppler_timeout_cleanup_policy": "kill-isolated-process-group",
        "clean_source_revision_bound": False,
        "python_stdlib_closure_bound": False,
        "python_dynamic_libraries_bound": False,
        "poppler_dynamic_libraries_bound": False,
        "operating_system_and_host_services_bound": False,
        "scope_complete": False,
        "complete": False,
        "enforceable": False,
        "incomplete_reasons": list(RAW_REFEREE_INCOMPLETE_REASONS),
        "future_gate_required": RAW_REFEREE_FUTURE_GATE,
    }


def _synthetic_comb_fixture(
        ) -> tuple[dict[str, Any], dict[str, Any], bytes, dict[str, Any]]:
    """Small, entirely in-memory v2 fixture for gate adversarial tests."""
    producer_records: dict[str, Any] = {}
    for relative in COMB_REFEREE_PRODUCERS:
        payload = relative.encode("utf-8")
        digest = sha256_bytes(payload)
        producer_records[relative] = {
            "path": relative, "bytes": len(payload), "sha256": digest,
            "head_sha256": digest, "equals_head": True,
        }
    python_path = str(pathlib.Path(sys.executable).resolve())
    python_digest = sha256_bytes(b"python")
    poppler_digest = sha256_bytes(b"pdftocairo")
    artifact_payloads = {
        "ir": b"ir", "layout": b"layout", "html": b"html",
        "guides": b"guide", "guide_html": b"guide-html",
        "asset": b"asset",
    }
    artifact_records = {
        "ir": {"path": "build/ir/fixture-1.ir.json", "bytes": 2,
               "sha256": sha256_bytes(artifact_payloads["ir"])},
        "layout": {"path": "build/layout/fixture-1.layout.json", "bytes": 6,
                   "sha256": sha256_bytes(artifact_payloads["layout"])},
        "html": {"path": "build/html/fixture-1.html", "bytes": 4,
                 "sha256": sha256_bytes(artifact_payloads["html"])},
        "guides": {"path": "build/guides/fixture-1.guide.json", "bytes": 5,
                   "sha256": sha256_bytes(artifact_payloads["guides"])},
        "guide_html": {
            "path": "build/html/fixture-1.guide.html", "bytes": 10,
            "sha256": sha256_bytes(artifact_payloads["guide_html"]),
        },
        "asset": {
            "path": "build/html/assets/fixture.png", "bytes": 5,
            "sha256": sha256_bytes(artifact_payloads["asset"]),
        },
    }
    provenance_payload = b"provenance"
    provenance_digest = sha256_bytes(provenance_payload)
    provenance_record = {
        "path": "forms/fixture-1/provenance.json",
        "bytes": len(provenance_payload),
        "sha256": provenance_digest,
        "head_sha256": provenance_digest,
        "equals_head": True,
    }
    source_payload = b"%PDF-fixture"
    source_digest = sha256_bytes(source_payload)
    source_candidate = {
        "path": "fixture.pdf", "bytes": len(source_payload),
        "sha256": source_digest,
    }
    layout_pin = {
        "file": "external:fixture.pdf", "sha256": source_digest,
        "bytes": len(source_payload), "page_count": 1,
    }
    source_relation = {
        "slug": "fixture-1",
        "declared_file": "fixture.pdf",
        "declared_sha256": source_digest,
        "declared_bytes": len(source_payload),
        "layout_pin": layout_pin,
        "candidate_count": 1,
        "matching_count": 1,
        "selected": "fixture.pdf",
        "candidates": [source_candidate],
    }
    fixture_subject = {
        "subject_key": "p1@0,0,10,10",
        "legacy_cell_id": "p1c1",
        "legacy_bbox": [0.0, 0.0, 10.0, 10.0],
        "cell_id": "p1c1",
        "mapped_partition_cell_ids": ["p1c1"],
        "state": "active_resolved",
        "blocks_gate": False,
        "reason_codes": [],
        "cells": 2,
    }
    fixture_comb = {
        "cells": 2,
        "divider_count": 1,
        "divider_x": [5.0],
        "slot_x": [0.0, 5.0, 10.0],
        "y0": 0.0,
        "y1": 10.0,
        "pitch_pt": 5.0,
        "resolution": {"status": "resolved", "reason_codes": []},
    }
    fixture_topology = _project_layout_topology(
        fixture_comb, [0.0, 0.0, 10.0, 10.0], "fixture topology")
    # A comb is bounded by its own printed rails, so slot_x's outer values need
    # not be the subject rectangle: it may start inside it, and it may sit a
    # fraction of a point beyond it, because that rectangle's x is the mean
    # centre of every collinear bar on the line. What it may not do is own a
    # compartment centred outside the rectangle.
    for railed in ([2.0, 5.0, 10.0], [0.0, 5.0, 8.0],
                   [-0.4, 5.0, 10.4], [2.0, 5.0, 8.0]):
        _project_layout_topology(
            {**fixture_comb, "slot_x": railed, "divider_x": [railed[1]]},
            [0.0, 0.0, 10.0, 10.0], "railed fixture topology")
    for stolen in ([-5.0, 0.0, 5.0], [5.0, 10.0, 15.0],
                   [10.0, 15.0, 20.0], [-10.0, -5.0, 0.0]):
        try:
            _project_layout_topology(
                {**fixture_comb, "slot_x": stolen, "divider_x": [stolen[1]]},
                [0.0, 0.0, 10.0, 10.0], "stolen fixture topology")
        except CombRefereeScopeError:
            continue
        raise AssertionError(
            "a comb with a compartment centred outside its subject passed: "
            f"{stolen}")
    fixture_emission_geometry = {
        "page_index": 1,
        "left": 0.0,
        "top": 0.0,
        "width": 10.0,
        "height": 10.0,
        "slots": [
            {
                "index": 0, "left": 0.0, "top": 0.0,
                "width": 5.0, "height": 10.0,
            },
            {
                "index": 1, "left": 5.0, "top": 0.0,
                "width": 5.0, "height": 10.0,
            },
        ],
    }
    fixture_emitted_evidence = {
        "count": 2,
        "indexes": [0, 1],
        "editable_indexes": [0, 1],
        "declared_capacity": 2,
        "declared_count": 2,
        "page_index": 1,
        "container_position": [0.0, 0.0],
        "container_geometry": [10.0, 10.0],
        "layout_binding_valid": True,
        "expected_geometry": fixture_emission_geometry,
        "slot_geometry": fixture_emission_geometry["slots"],
        "valid": True,
    }
    fixture_lattice_evidence = {
        "file": "tools/formgen/lattice.py",
        "bytes": producer_records["tools/formgen/lattice.py"]["bytes"],
        "sha256": producer_records["tools/formgen/lattice.py"]["sha256"],
        "expected_sha256": producer_records[
            "tools/formgen/lattice.py"]["sha256"],
        "layout_generator": {"fixture": True},
    }
    assertion_relation = {
        "combs_expected": 1,
        "combs_checked": 1,
        "expected_comb_ids": ["p1c1"],
        "checked_comb_ids": ["p1c1"],
        "emitted_comb_ids": ["p1c1"],
        "unexpected_emitted_comb_ids": [],
        "duplicate_layout_comb_ids": [],
        "duplicate_emitted_cell_ids": [],
        "raw_live_comb_issues": 0,
        "emitted_cell_binding_issues": 0,
        "inventory_complete": True,
        "layout_mismatches": 0,
        "layout_unevaluable": 0,
        "owner_certificates_valid": 1,
        "owner_certificates_invalid": 0,
        "source_u_frame_evaluable": 0,
        "source_certified_unframed_evaluable": 1,
        "emission_behind_layout": 0,
        "emission_invalid": 0,
        # Z1's declared schema change (see AUDIT_ASSERTION_SUMMARY_KEYS).
        "decided_by_review": 0,
        "decided_by_review_subjects": [],
        "offender_count": 0,
        "offenders_published": 0,
        "offenders_omitted": 0,
        "offender_dimensions": {},
        "holds": True,
    }
    audit_inputs = {
        "ir": {
            "file": "fixture-1.ir.json", "required": True, "present": True,
            "bytes": artifact_records["ir"]["bytes"],
            "sha256": artifact_records["ir"]["sha256"],
        },
        "layout": {
            "file": "fixture-1.layout.json", "required": True, "present": True,
            "bytes": artifact_records["layout"]["bytes"],
            "sha256": artifact_records["layout"]["sha256"],
        },
        "html": {
            "file": "fixture-1.html", "required": True, "present": True,
            "bytes": artifact_records["html"]["bytes"],
            "sha256": artifact_records["html"]["sha256"],
        },
        "guide": {
            "file": "fixture-1.guide.json", "required": True, "present": True,
            "bytes": artifact_records["guides"]["bytes"],
            "sha256": artifact_records["guides"]["sha256"],
        },
        "guide_html": {
            "file": "fixture-1.guide.html", "required": False, "present": True,
            "bytes": artifact_records["guide_html"]["bytes"],
            "sha256": artifact_records["guide_html"]["sha256"],
        },
        "source_pdf": {
            "file": "fixture.pdf",
            "logical_identity": "external:fixture.pdf",
            "path": "fixture.pdf",
            "required": True,
            "present": True,
            "bytes": len(source_payload),
            "sha256": source_digest,
            "expected_sha256": source_digest,
        },
    }
    fixture_render_dependency = {
        "path": "assets/fixture.png", "mime_type": "image/png",
        "present": True, "bytes": artifact_records["asset"]["bytes"],
        "sha256": artifact_records["asset"]["sha256"],
        "kinds": ["img"], "referrers": ["fixture-1.html"],
    }
    audit_render = {
        "entrypoint": "fixture-1.html",
        "dependencies": [fixture_render_dependency],
        "errors": [], "complete": True,
        "network_policy": "deny-except-retained-relative-resources-and-inline-data",
    }
    audit_form_relation = {
        "record_sha256": sha256_bytes(b"audit-record"),
        "input_manifest_sha256": sha256_bytes(b"audit-manifest"),
        "inputs": audit_inputs,
        "render": audit_render,
        "assertion_sha256": sha256_bytes(b"audit-assertion"),
        "assertion_relation": assertion_relation,
        "top_level_holds": True,
    }
    synthetic_audit_forms = {"fixture-1": audit_form_relation}
    synthetic_audit_forms.update({
        f"unused-{index}": {"record_sha256": sha256_bytes(
            f"unused-{index}".encode("utf-8"))}
        for index in range(2, EXPECTED_FORMS + 1)
    })
    audit_digest = sha256_bytes(b"audit")
    html_files = [
        artifact_records["html"], artifact_records["guide_html"],
        artifact_records["asset"],
    ]
    fixture_dependency_entries = [
        {
            "name": name,
            "root": "/synthetic",
            "kind": "directory",
            "files": [{
                "path": "__init__.py", "type": "file", "mode": 0o444,
                "bytes": len(name), "sha256": sha256_bytes(name.encode()),
            }],
        }
        for name in ("fitz", "pymupdf")
    ]
    fixture_dependency_entries.append({
        "name": "playwright",
        "root": "/synthetic",
        "kind": "directory",
        "files": [
            {
                "path": "__init__.py", "type": "file", "mode": 0o444,
                "bytes": 10, "sha256": sha256_bytes(b"playwright"),
            },
            {
                "path": "driver/chromium", "type": "file", "mode": 0o555,
                "bytes": 8, "sha256": sha256_bytes(b"chromium"),
            },
        ],
    })
    snapshot: dict[str, Any] = {
        "git": {
            "commit": "1" * 40,
            "tree": "2" * 40,
            "worktree_clean": True,
        },
        "producers": producer_records,
        "runtime": {
            "python": {
                "path": python_path, "bytes": 6,
                "sha256": python_digest,
            },
            "python_identity": {
                "implementation": platform.python_implementation(),
                "version": platform.python_version(),
                "cache_tag": str(sys.implementation.cache_tag),
            },
            "python_runtime_library": None,
            "python_dependencies": _isolated_dependency_manifest(
                fixture_dependency_entries),
            "python_dependency_files": _isolated_dependency_file_projection(
                fixture_dependency_entries),
            "python_dependency_trees": _isolated_dependency_tree_projections(
                fixture_dependency_entries),
            "pymupdf_distribution_version": "fixture",
            "playwright_distribution_version": "fixture",
            "pdftocairo": {
                "path": "/trusted/pdftocairo", "bytes": 10,
                "sha256": poppler_digest,
            },
        },
        "audit": {
            "path": "build/audit.json", "bytes": 5, "sha256": audit_digest,
            "form_count": EXPECTED_FORMS,
            "forms_sha256": canonical_digest(synthetic_audit_forms),
            "forms": synthetic_audit_forms,
            "application_attestation": {
                "path": "build/audit-attested.json",
                "bytes": 10,
                "sha256": sha256_bytes(b"audit-app"),
            },
            "application_scope_attested": True,
        },
        "artifact_trees": {
            "ir": {**_file_manifest([artifact_records["ir"]]), "root": "build/ir"},
            "layout": {
                **_file_manifest([artifact_records["layout"]]),
                "root": "build/layout",
            },
            "html": {**_file_manifest(html_files), "root": "build/html"},
            "guides": {
                **_file_manifest([artifact_records["guides"]]),
                "root": "build/guides",
            },
        },
        "layout_bindings": {
            "fixture-1": {
                "layout_sha256": artifact_records["layout"]["sha256"],
                "guide_sha256": artifact_records["guides"]["sha256"],
                "lattice_evidence": fixture_lattice_evidence,
                "audit_expected_ids": ["p1c1"],
                "cells": {
                    "p1c1": {
                        "cell": "p1c1",
                        "subject_key": fixture_subject["subject_key"],
                        "legacy_cell_id": "p1c1",
                        "cell_id": "p1c1",
                        "ledger_state": "active_resolved",
                        "ledger_blocks_gate": False,
                        "ledger_reason_codes": [],
                        "ledger_topology_sha256": fixture_topology["sha256"],
                        "ledger_evidence": fixture_subject,
                        "page": 1,
                        "stream_index": 0,
                        "bbox": [0.0, 0.0, 10.0, 10.0],
                        "latticed": 2,
                        "lattice_divider_x": [5.0],
                        "expected_emission_geometry": (
                            fixture_emission_geometry),
                    },
                },
                "inferences": {},
            },
        },
        "render_bindings": {
            "fixture-1": {
                "html_sha256": artifact_records["html"]["sha256"],
                **audit_render,
            },
        },
        "provenance": _file_manifest([provenance_record]),
        "source_pdfs": {
            "relation_count": 1, "candidate_file_count": 1,
            "sha256": canonical_digest([source_relation]),
            "relations": [source_relation],
        },
    }
    fixture_manifest_reason = "complete"
    fixture_manifest_binding = {
        "binding_valid": True,
        "manifest_inputs_complete": True,
        "attestation_complete": True,
        "enforceable": True,
        "complete": True,
        "reason": fixture_manifest_reason,
        "errors": [],
        "blockers": list(RAW_AUDIT_SCOPE_BLOCKERS),
        "host_scope_boundaries": [
            "fixture host trusted computing base is out of scope"],
        "producer_sha256": producer_records[
            "tools/formgen/audit.py"]["sha256"],
        "runtime_tree_sha256": sha256_bytes(b"audit-runtime-tree"),
        "runtime_manifest_self_consistent": True,
        "base_runtime_closure_independently_attested": True,
        "roundtrip_runtime_closure_independently_attested": True,
        "render_dependency_count": 1,
        "render_dependencies": [fixture_render_dependency],
        "roundtrip_present": True,
    }
    fixture_ledger_binding = {
        "binding_valid": True,
        "reason": "complete",
        "errors": [],
        "active_subject_ids": ["p1c1"],
        "emitted_ids": ["p1c1"],
        "legacy_alias_count": 1,
    }
    comparisons = {name: 0 for name in COMPARISON_NAMES}
    comparisons["agree"] = 1
    cell = {
        "cell": "p1c1",
        "subject_key": "p1@0,0,10,10",
        "legacy_cell_id": "p1c1",
        "cell_id": "p1c1",
        "ledger_state": "active_resolved",
        "ledger_blocks_gate": False,
        "ledger_reason_codes": [],
        "ledger_topology_sha256": fixture_topology["sha256"],
        "ledger_evidence": fixture_subject,
        "page": 1,
        "bbox": [0.0, 0.0, 10.0, 10.0],
        "latticed": 2,
        "lattice_divider_x": [5.0],
        "emitted": 2,
        "emitted_indexes_valid": True,
        "emitted_evidence": fixture_emitted_evidence,
        "audit_printed": 2,
        "audit_relation": "complete-non-offender",
        "comparison_reason": "referee, lattice, audit, and emitted agree",
        "comparison_status": "agree",
        "transition_status": "none",
        "transition_reason": "active ledger subject is already resolved",
        # C4a: every cell publishes the key; null means "no reviewed
        # resolution promoted this subject".
        "resolution_certificate": None,
        "transition_certificate": None,
        "exception_registry_key": None,
        "referee": {
            "status": "measured",
            "reason": (
                "one source topology contains every recognised anchor"),
            "y0": 0.0,
            "y1": 10.0,
            "source_divider_x": [5.0],
            "source_rail_x": [0.0, 10.0],
            "rail_derivation": {
                "left": {"basis": "owner-edge"},
                "right": {"basis": "owner-edge"},
            },
            "extra_divider_x": [],
            "compartments": 2,
            "anchor_matches": [{
                "layout_x": 5.0,
                "source_x": 5.0,
                "delta_pt": 0.0,
            }],
            "positions_match": True,
            "anchors_complete": True,
            "subject_gap_proofs": [],
            "unproven_subject_gaps": [],
            "components": [{
                "x": 5.0,
                "x0": 4.9,
                "x1": 5.1,
                "tone": 0.0,
                "elements": ["fixture-divider"],
                "clipped": False,
            }],
            "contract_y0": 0.0,
            "contract_y1": 10.0,
            "open_y0": 0.0,
            "open_y1": 10.0,
            "contract_span_pt": 10.0,
            "seed_span_pt": 10.0,
            "measured_span_pt": 10.0,
            "unmeasured_span_pt": 0.0,
            "topology_coverage_pt": {"5.0": 10.0},
            "ignored_slabs": [],
            "chosen_topology": [5.0],
            "topology_superset_relations": [],
        },
        "four_way": {
            "referee": 2, "lattice": 2, "audit": 2, "emitted": 2,
        },
    }
    form_counts = {
        "combs": 1,
        "subjects": 1,
        "subjects_active": 1,
        "subjects_active_resolved": 1,
        "subjects_active_unresolved": 0,
        "subjects_retained_unresolved": 0,
        "inferences_suppressed": 0,
        "ledger_blocking": 0,
        "ledger_blocking_excused": 0,
        "measured": 1,
        "composite": 0,
        "source_unevaluable": 0,
        "unevaluable": 0,
        "referee_layout_mismatches": 0,
        "referee_layout_position_mismatches": 0,
        "emission_layout_mismatches": 0,
        "comparisons": comparisons,
    }
    fixture_poppler = {
        "version": "fixture-poppler",
        "binary_path": "/trusted/pdftocairo",
        "binary_sha256": poppler_digest,
        "identity_timeout_seconds": 10.0,
        "page_timeout_seconds": 60.0,
        "subprocess_cleanup_policy": "kill-isolated-process-group",
    }
    form = {
        "slug": "fixture-1",
        "status": "ok",
        "reason": "all combs measured",
        "source": {
            "file": "fixture.pdf",
            "sha256": source_digest,
            "bytes": len(source_payload),
            "page_count": 1,
            "layout_pin": layout_pin,
        },
        "artifacts": {
            "ir_sha256": artifact_records["ir"]["sha256"],
            "layout_sha256": artifact_records["layout"]["sha256"],
            "html_sha256": artifact_records["html"]["sha256"],
            "html_structure_sha256": artifact_records["html"]["sha256"],
            "guide_sha256": artifact_records["guides"]["sha256"],
            "guide_html_sha256": artifact_records["guide_html"]["sha256"],
            "tracked_provenance_file": provenance_record["path"],
            "tracked_provenance_sha256": provenance_digest,
        },
        "lattice_evidence": fixture_lattice_evidence,
        "poppler": fixture_poppler,
        "pages": [{
            "page": 1,
            "svg_sha256": sha256_bytes(b"fixture-svg"),
            "vector_paints": 1,
            "unsupported_regions": 0,
        }],
        "audit_evidence": {
            **assertion_relation,
            "complete": True,
            "reason": "complete",
            "errors": [],
            "assertion_valid": True,
            "input_manifest_verified": True,
            "input_manifest_reason": fixture_manifest_reason,
            "evidence_published": True,
            "byte_and_relation_binding_valid": True,
            "manifest_binding": fixture_manifest_binding,
            "ledger_binding": fixture_ledger_binding,
            "runtime_closure_independently_attested": True,
            "integrity_valid": True,
        },
        "emission_inventory": {
            "complete": True,
            "reason": "complete",
            "expected_active_cell_ids": ["p1c1"],
            "emitted_cell_ids": ["p1c1"],
            "missing_active_cell_ids": [],
            "unexpected_emitted_cell_ids": [],
            "retained_emitted_cell_ids": [],
            "inference_emitted_cell_ids": [],
            "invalid_active_cell_ids": [],
        },
        "emission_binding_errors": [],
        "counts": form_counts,
        "inferences": [],
        "cells": [cell],
    }
    audit_record = producer_records["tools/formgen/audit.py"]
    lattice_record = producer_records["tools/formgen/lattice.py"]
    referee_record = producer_records["tools/formgen/comb_referee.py"]
    report: dict[str, Any] = {
        "schema_version": COMB_REFEREE_REPORT_VERSION,
        "producer": "tools/formgen/comb_referee.py",
        "producer_sha256": referee_record["sha256"],
        "python_version": "fixture",
        "provenance": {
            "producer": {
                "file": "tools/formgen/comb_referee.py",
                "bytes": referee_record["bytes"],
                "sha256": referee_record["sha256"],
            },
            "dependencies": {
                "audit": {
                    "file": "tools/formgen/audit.py",
                    "bytes": audit_record["bytes"],
                    "sha256": audit_record["sha256"],
                    "expected_sha256": audit_record["sha256"],
                    "dependencies": [
                        {
                            "file": relative,
                            "bytes": producer_records[relative]["bytes"],
                            "sha256": producer_records[relative]["sha256"],
                            "expected_sha256": (
                                producer_records[relative]["sha256"]),
                        }
                        for relative in (
                            "tools/formgen/extract.py",
                            "tools/formgen/verify.py",
                        )
                    ],
                },
                "lattice": {
                    "file": "tools/formgen/lattice.py",
                    "bytes": lattice_record["bytes"],
                    "sha256": lattice_record["sha256"],
                    "expected_sha256": lattice_record["sha256"],
                },
            },
            "runtime": {
                "python_implementation": "cpython",
                "python_version": "fixture",
                "python_executable": python_path,
                "python_executable_sha256": python_digest,
                "poppler": fixture_poppler,
            },
        },
        "status": "unevaluable",
        "status_reasons": [
            "standalone referee runtime/application attestation is incomplete "
            "and non-enforceable",
        ],
        "attestation": _producer_raw_referee_attestation_fixture(),
        "poppler": fixture_poppler,
        "inputs": {
            "audit_sha256": audit_digest,
            "audit_bytes": 5,
            # report_binding_errors checks the production full-corpus binding.
            "layout_count": EXPECTED_FORMS,
        },
        "totals": {
            "forms_expected": 1,
            "forms_measured": 1,
            "forms_error": 0,
            "combs_expected": 1,
            "combs_found": 1,
            "combs_measured": 1,
            "combs_composite": 0,
            "combs_unevaluable": 0,
            "combs_source_unevaluable": 0,
            "subjects_active": 1,
            "subjects_active_resolved": 1,
            "subjects_active_unresolved": 0,
            "subjects_retained_unresolved": 0,
            "inferences_suppressed": 0,
            "ledger_blocking": 0,
            "ledger_blocking_excused": 0,
            "referee_layout_mismatches": 0,
            "referee_layout_position_mismatches": 0,
            "comparisons": comparisons,
            "forms_ok": 1,
            "forms_disagreement": 0,
            "forms_unevaluable": 0,
            "audit_evidence_complete_forms": 1,
            "referee_attestation_complete": False,
            "referee_enforceable": False,
        },
        "errors": [],
        "forms": [form],
    }
    attach_self_digest(report)
    raw = (json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False)
           + "\n").encode("utf-8")
    relations = {name: True for name in ENVELOPE_RELATIONS}
    envelope: dict[str, Any] = {
        "schema_version": COMB_REFEREE_ATTESTATION_VERSION,
        "application_scope_name": COMB_REFEREE_SCOPE,
        "application_snapshot": snapshot,
        "invocation": {
            "executable": sys.executable,
            "resolved_executable": python_path,
            "python_flags": list(ISOLATED_PYTHON_ATTESTED_FLAGS),
            "pythonpath_removed": True,
            "pythonhome_removed": True,
            "timeout_seconds": COMB_REFEREE_TIMEOUT_SECONDS,
            "total_timeout_seconds": COMB_REFEREE_TOTAL_TIMEOUT_SECONDS,
            "run_count": COMB_REFEREE_RUN_COUNT,
            "child_exits": [2, 2],
            "output": "private-temporary-output",
            "child_exit": 2,
        },
        "raw_report": {
            "file": "build/comb-referee.json",
            "bytes": len(raw),
            "sha256": sha256_bytes(raw),
            "payload_sha256": report["payload_sha256"],
            "schema_version": report["schema_version"],
            "status": report["status"],
            "repeat_sha256": [sha256_bytes(raw)] * COMB_REFEREE_RUN_COUNT,
        },
        "relations": relations,
        "host_tcb_required": True,
        "host_scope_complete": False,
        "host_closure_claimed": False,
        "operating_system_bound": False,
        "python_stdlib_bound": False,
        "dynamic_libraries_bound": False,
        "application_scope_complete": True,
        "enforceable": True,
        "enforcement_scope": "application-only",
    }
    attach_self_digest(envelope)
    return report, snapshot, raw, envelope


def _synthetic_batch_record(slug: str) -> dict[str, Any]:
    code, revision = slug.rsplit("-", 1)
    source_file = f"{slug}.pdf"
    source_digest = sha256_bytes(slug.encode("utf-8"))
    return {
        "slug": slug,
        "code": code.upper(),
        "revision": revision,
        "variant": "",
        "in_corpus": True,
        "source_file": source_file,
        "sha256": source_digest,
        "stage_failed": None,
        "error": None,
        "images_extracted": 0,
        "pages": 1,
        "paper": "612.0x792.0",
        "uniform_paper": True,
        "page_papers": ["612.0x936.0"],
        "fonts": [],
        "rules": 0,
        "text_runs": 0,
        "images": 0,
        "cells": 0,
        "comb_cells": 0,
        "growables": [],
        "sources": [{
            "role": "form", "file": source_file, "sha256": source_digest,
        }],
        "guide": None,
        "guide_detected": {"inline_pages": [], "standalone_pdfs": []},
        "html_bytes": 1,
        "html": f"build/html/{slug}.html",
        "guide_build": {"plan": f"build/guides/{slug}.guide.json",
                        "html": None, "pdfs": []},
        "guide_source_irs": [],
        "font_plans": [f"build/fonts/{slug}.fontplan.json"],
        "asset_digests": {},
    }


def _synthetic_audit_record(
        slug: str, application_scope: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
    comb_assertion = {
        "holds": True,
        "reason": "",
        "offenders": [],
        "combs_expected": 0,
        "combs_checked": 0,
        "expected_comb_ids": [],
        "checked_comb_ids": [],
        "emitted_comb_ids": [],
        "unexpected_emitted_comb_ids": [],
        "duplicate_layout_comb_ids": [],
        "duplicate_emitted_cell_ids": [],
        "raw_live_comb_issues": 0,
        "emitted_cell_binding_issues": 0,
        "inventory_complete": True,
        "layout_mismatches": 0,
        "layout_unevaluable": 0,
        "owner_certificates_valid": 0,
        "owner_certificates_invalid": 0,
        "source_u_frame_evaluable": 0,
        "source_certified_unframed_evaluable": 0,
        "emission_behind_layout": 0,
        "emission_invalid": 0,
        # Z1's declared schema change: published unconditionally by audit.py,
        # so the fixture must carry it or the validator that now requires it
        # would never be exercised on a well-formed record.
        "decided_by_review": 0,
        "decided_by_review_subjects": [],
    }
    basic_counts = {
        "inputs_over_printed_text": {
            "cells_checked": 0, "emitted_cell_binding_issues": 0},
        "money_boxes_have_inputs": {
            "boxes_checked": 0, "combs_fully_inked": 0,
            "boxes_preprinted": 0,
            "emitted_cell_binding_issues": 0},
        "rules_below_guide_cut": {
            "cuts": 0, "area_fills_below_cut": 0},
        "run_colour_matches_ir": {"runs_checked": 0},
        "reflow_rate_without_description": {
            "rate_tables": 0, "rows_checked": 0},
        "image_transform_applied": {
            "placements": 0, "relocated_placements": 0},
        "no_invented_codepoints": {"characters_examined": 0},
        "inputs_span_no_printed_divider": {
            "inputs_checked": 0, "printed_dividers_detected": 0,
            "emitted_cell_binding_issues": 0},
        "printed_box_peers_all_fillable": {
            "printed_boxes_checked": 0, "peer_rows_checked": 0,
            "boxes_unevaluable": 0, "emitted_cell_binding_issues": 0},
    }
    assertions = {
        key: (comb_assertion if key == "comb_slots_match_printed" else {
            "holds": True, "reason": "", "offenders": [],
            **basic_counts[key],
        })
        for key in REQUIRED_ASSERTIONS
    }
    if application_scope is None:
        producer_records = {
            relative: {
                "path": relative,
                "bytes": len(relative.encode("utf-8")),
                "sha256": sha256_bytes(relative.encode("utf-8")),
            }
            for relative in (
                "tools/formgen/audit.py", "tools/formgen/extract.py",
                "tools/formgen/verify.py")
        }
        python_record = {
            "bytes": 6, "sha256": sha256_bytes(b"python")}
        python_identity = {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "cache_tag": str(sys.implementation.cache_tag),
        }
        dependency_members = {
            "fitz/__init__.py": {
                "bytes": 4, "sha256": sha256_bytes(b"fitz")},
            "pymupdf/__init__.py": {
                "bytes": 7, "sha256": sha256_bytes(b"pymupdf")},
            "playwright/driver/chromium": {
                "bytes": 8, "sha256": sha256_bytes(b"chromium")},
        }
        input_digest = sha256_bytes(slug.encode("utf-8"))
        inputs = {
            role: {
                "file": f"{slug}.{suffix}", "required": True,
                "present": True, "bytes": 1, "sha256": input_digest,
            }
            for role, suffix in (
                ("ir", "ir.json"), ("layout", "layout.json"),
                ("html", "html"), ("guide", "guide.json"))
        }
        inputs["guide_html"] = {
            "file": f"{slug}.guide.html", "required": False,
            "present": False, "bytes": None, "sha256": None,
        }
        inputs["source_pdf"] = {
            "file": f"{slug}.pdf", "logical_identity": f"external:{slug}.pdf",
            "path": f"{slug}.pdf", "required": True, "present": True,
            "bytes": 1, "sha256": input_digest,
            "expected_sha256": input_digest,
        }
        render = {
            "entrypoint": f"{slug}.html", "dependencies": [],
            "errors": [], "complete": True,
            "network_policy": (
                "deny-except-retained-relative-resources-and-inline-data"),
        }
    else:
        producer_records = application_scope["producers"]
        snapshot_runtime = application_scope["runtime"]
        python_record = snapshot_runtime["python"]
        python_identity = snapshot_runtime["python_identity"]
        dependency_members = {
            member["path"]: member
            for member in snapshot_runtime["python_dependency_files"]["members"]
        }
        audit_form = application_scope["audit"]["forms"][slug]
        inputs = json.loads(json.dumps(audit_form["inputs"]))
        render = json.loads(json.dumps(audit_form["render"]))
    producer = {
        "file": "tools/formgen/audit.py",
        "bytes": producer_records["tools/formgen/audit.py"]["bytes"],
        "sha256": producer_records["tools/formgen/audit.py"]["sha256"],
        "dependencies": [
            {
                "file": relative,
                "bytes": producer_records[relative]["bytes"],
                "sha256": producer_records[relative]["sha256"],
                "loaded_origin": relative,
                "executed_from_snapshotted_source": True,
            }
            for relative in (
                "tools/formgen/extract.py", "tools/formgen/verify.py")
        ],
        "dependency_execution_bound": True,
        "audit_execution_bound": False,
        "assertion_producer_bound": False,
        "roundtrip_runtime_bound_in_record": False,
        "standalone_attestation_complete": False,
        "incomplete_reason": "synthetic standalone producer scope is incomplete",
    }
    runtime_members = [
        {
            "file": "module/fitz",
            "bytes": dependency_members["fitz/__init__.py"]["bytes"],
            "sha256": dependency_members["fitz/__init__.py"]["sha256"],
        },
        {
            "file": "module/pymupdf",
            "bytes": dependency_members["pymupdf/__init__.py"]["bytes"],
            "sha256": dependency_members["pymupdf/__init__.py"]["sha256"],
        },
        {
            "file": "python/executable",
            "bytes": python_record["bytes"],
            "sha256": python_record["sha256"],
        },
    ]
    runtime_tuples = [
        (member["file"], member["bytes"], member["sha256"])
        for member in runtime_members]
    runtime_payload = json.dumps(
        runtime_tuples, separators=(",", ":")).encode("ascii")
    application_closure = {
        "scope": AUDIT_APPLICATION_CLOSURE_SCOPE,
        "algorithm": AUDIT_TREE_CLOSURE_ALGORITHM,
        "bytecode_caches_excluded": True,
        "exclusion_reason": "synthetic closure mirrors the published exclusion",
        "packages": [
            {
                "logical_root": name,
                "algorithm": AUDIT_TREE_CLOSURE_ALGORITHM,
                "files": 2,
                "symlinks": 0,
                "bytes": 64,
                "tree_sha256": sha256_bytes(
                    f"synthetic-{name}-tree".encode("ascii")),
            }
            for name in ("fitz", "pymupdf")
        ],
        "modules": [
            {
                "module": member["file"][len("module/"):],
                "file": f"{member['file'][len('module/'):]}/__init__.py",
                "bytes": member["bytes"],
                "sha256": member["sha256"],
            }
            for member in runtime_members
            if member["file"].startswith("module/")
        ],
        "native_libraries": [{
            "file": "pymupdf/libmupdf.dylib",
            "bytes": 32,
            "sha256": sha256_bytes(b"synthetic-libmupdf"),
        }],
        "unbound_modules": [],
        "validated_before_after": True,
        "complete": True,
    }
    runtime = {
        "python": dict(python_identity),
        "pymupdf": {"package_version": "fixture", "version_bind": "fixture"},
        "loaded_application_files": {
            "algorithm": "sha256(canonical-json(logical-file,bytes,sha256))",
            "files": len(runtime_members),
            "bytes": sum(member["bytes"] for member in runtime_members),
            "tree_sha256": sha256_bytes(runtime_payload),
            "members": runtime_members,
            "validated_before_after": True,
        },
        "application_closure": application_closure,
        "stdlib_and_system_shared_libraries_bound": False,
        "scope_complete": False,
        "incomplete_reason": "synthetic host runtime scope is incomplete",
    }
    if application_scope is None:
        playwright_closure = {
            "logical_root": "playwright",
            "algorithm": "sha256(canonical-json(path,type,bytes,digest))",
            "files": 1, "symlinks": 0, "bytes": 8,
            "tree_sha256": sha256_bytes(b"synthetic-playwright-tree"),
        }
        playwright_version = "fixture"
    else:
        playwright_closure = application_scope["runtime"][
            "python_dependency_trees"]["trees"]["playwright"]
        playwright_version = application_scope["runtime"][
            "playwright_distribution_version"]
    chromium = dependency_members["playwright/driver/chromium"]
    roundtrip_runtime = {
        "mode": "playwright-exact-executable",
        "playwright_package_version": playwright_version,
        "dependency_closure": dict(playwright_closure),
        "chromium": {
            "file": "playwright/driver/chromium",
            "bytes": chromium["bytes"], "sha256": chromium["sha256"],
            "version_output": "Chromium 147.0.0.0",
        },
        "same_resolution_session_used_for_render": True,
        "dependency_closure_validated_before_after": True,
        "system_shared_libraries_bound": False,
        "native_host_environment_bound": False,
        "scope": AUDIT_ROUNDTRIP_SCOPE,
        "scope_complete": False,
        "incomplete_reason": "synthetic native host scope is incomplete",
        "live_browser_version": "147.0.0.0",
        "explicit_executable_path_used": True,
        "launch_args": list(AUDIT_ROUNDTRIP_LAUNCH_ARGS),
        "service_workers": "block",
        "browser_context_offline": True,
        "websocket_policy": "record-and-leave-unconnected",
        "request_policy": "formgen-snapshot-only-v1",
        "playwright_operation_timeout_ms": 120000,
        "hard_deadline_seconds": 60.0,
        "hard_deadline_enforced_by": "isolated-render-worker-process-v1",
        "deadline_cleanup_policy": "kill-worker-and-chromium-process-group",
    }
    fulfilled = sorted({
        render["entrypoint"],
        *(item["path"] for item in render["dependencies"]),
    })
    render_requests = {
        "policy": "formgen-snapshot-only-v1",
        "synthetic_origin": "https://formgen.invalid",
        "fulfilled": fulfilled,
        "fulfilled_requests": len(fulfilled),
        "blocked": [], "blocked_requests": 0, "blocked_websockets": [],
        "all_requests_from_retained_closure": True,
    }
    candidate_pdf = {
        "bytes": 1, "sha256": sha256_bytes(b"candidate-pdf"),
        "retained_exact_bytes": True,
        "chromium_returned_in_memory": True,
        "normalization": {
            "algorithm": "fixed-width-creation-modification-date-v1",
            "fields_normalized": 2,
            "replacement": AUDIT_PDF_NORMALIZATION_REPLACEMENT,
            "xref_offsets_preserved": True,
        },
        "materialization": AUDIT_CANDIDATE_MATERIALIZATION,
        "expected_sha256_passed_to_extractor": True,
        "validated_before_after_extraction": True,
        "candidate_ir_sha256": sha256_bytes(b"candidate-ir"),
        "candidate_ir_digest_scope": "source-and-generator-removed",
    }
    record: dict[str, Any] = {
        "slug": slug,
        "status": "ok",
        "error": None,
        "input_manifest": {
            "schema": "formgen-audit-input-manifest-v1",
            "algorithm": "sha256",
            "producer": producer,
            "runtime": runtime,
            "inputs_complete": True,
            "attestation_complete": True,
            "enforceable": True,
            "complete": True,
            "missing_required": [],
            "inputs": inputs,
            "render": render,
        },
        "provenance_validation": {
            "validated_before": True,
            "validated_after": True,
            "error": None,
        },
        "assertions": assertions,
        "assertions_held": len(REQUIRED_ASSERTIONS),
        "attestation": {
            "inputs_complete": True,
            "producer_execution_bound": False,
            "base_runtime_scope_complete": False,
            "roundtrip_runtime_scope_complete": False,
            "application_closure_complete": True,
            "validated_before_after": True,
            "complete": True,
            "enforceable": True,
            "incomplete_reasons": [],
            "declared_out_of_scope": ["synthetic host scope is out of scope"],
            "future_gate_required": "outer application wrapper",
        },
        "roundtrip_runtime": roundtrip_runtime,
        "render_requests": render_requests,
        "candidate_pdf": candidate_pdf,
        "measured": True,
        "hard_failure": None,
        "paper_ok": True,
        "rules_ref": 1,
        "rules_missing": 0,
        "rules_extra": 0,
        "rules_thickness_violations": 0,
        "rules_pct": 100.0,
        "images_missing": 0,
        "images_placement_violations": 0,
        "text_ref": 1,
        "text_missing": 0,
        "text_extra": 0,
        "text_pct": 100.0,
    }
    record.update({key: True for key in REQUIRED_ASSERTIONS})
    return record


def self_test() -> int:
    """Run the fixtures against EMPTY reviewed registries.

    The registries hold real user decisions (112 of them after the C4b
    sitting), while these fixtures build synthetic reports that contain none
    of the named cells -- so the corpus coverage guard would report every
    real entry as applied nowhere, inside a fixture that never claimed to
    apply it. The guard is exercised deliberately by its own fixtures below.
    """
    registry = _load_review_registry()
    saved_r = dict(registry.REVIEWED_LEDGER_RESOLUTIONS)
    saved_t = dict(registry.REVIEWED_LEDGER_TRANSITIONS)
    saved_e = dict(registry.REVIEWED_UNEVALUABLE_EXCEPTIONS)
    registry.REVIEWED_LEDGER_RESOLUTIONS.clear()
    registry.REVIEWED_LEDGER_TRANSITIONS.clear()
    registry.REVIEWED_UNEVALUABLE_EXCEPTIONS.clear()
    try:
        return _self_test_body()
    finally:
        registry.REVIEWED_UNEVALUABLE_EXCEPTIONS.update(saved_e)
        registry.REVIEWED_LEDGER_RESOLUTIONS.update(saved_r)
        registry.REVIEWED_LEDGER_TRANSITIONS.update(saved_t)


def _self_test_body() -> int:
    """Prove the gate can fail, and that it treats absence as failure.

    A gate that cannot fail is worthless, and one that passes on a missing check
    is worse than none at all -- so both properties are asserted rather than
    assumed.
    """
    failures = []
    if Verdict.UNEVALUABLE.ok:
        failures.append("UNEVALUABLE must not count as ok")
    if not Verdict.PASS.ok:
        failures.append("PASS must count as ok")

    for status in ("D ", " D", "MD", "RD", "CD", "AD"):
        path = f"forms/deleted-{status.replace(' ', '_')}.json"
        result = _tracked_deletion_result(f"{status} {path}\n")
        if result.verdict is not Verdict.FAIL or path not in result.detail:
            failures.append(
                f"porcelain deletion state {status!r} must fail closed")
    non_deletion_statuses = (
        "M ", " M", "MM", "A ", "AM", "R ", "RM", "C ", "CM",
        "??", "!!", "UU",
    )
    non_deletion_fixture = "".join(
        f"{status} forms/retained-{index}.json\n"
        for index, status in enumerate(non_deletion_statuses))
    if _tracked_deletion_result(
            non_deletion_fixture).verdict is not Verdict.PASS:
        failures.append(
            "porcelain states without D in either XY column must not fail")

    scanner = GateAuditRenderDependencyScanner()
    scanner.feed(
        '<script src="assets/runtime.js"></script>'
        '<base href="https://example.invalid/">'
        '<meta http-equiv="refresh" content="0;url=elsewhere.html">')
    scanner.close()
    if ("assets/runtime.js", "script") not in scanner.references:
        failures.append(
            "independent audit render closure must include script sources")
    if not any("base href" in error for error in scanner.errors):
        failures.append(
            "independent audit render closure must reject base href")
    if not any("meta refresh" in error for error in scanner.errors):
        failures.append(
            "independent audit render closure must reject meta refresh")
    srcset_scanner = GateAuditRenderDependencyScanner()
    srcset_scanner.feed('<img srcset="data:image/png;base64,AAAA 1x">')
    srcset_scanner.close()
    if not srcset_scanner.errors:
        failures.append(
            "ambiguous data-URL srcset must make render closure unevaluable")

    for malformed_runtime in (None, [], "runtime", 1):
        malformed_envelope = {
            "application_snapshot": {"runtime": malformed_runtime},
            "invocation": {}, "raw_report": {},
        }
        try:
            malformed_errors = validate_audit_application_envelope(
                malformed_envelope, b"[]")
        except Exception as error:  # noqa: BLE001 - regression probe
            failures.append(
                "malformed audit application runtime must not raise: "
                f"{type(error).__name__}: {error}")
        else:
            if not malformed_errors:
                failures.append(
                    "malformed audit application runtime must fail closed")

    findings_fixture = {
        "schema_version": 1, "source": "synthetic", "note": "synthetic",
        "cause_codes": {}, "findings": None,
    }
    if not findings_payload_errors(findings_fixture):
        failures.append("a non-list findings inventory must fail closed")
    findings_fixture["findings"] = [None]
    if not findings_payload_errors(findings_fixture):
        failures.append("a non-object finding must fail closed")
    findings_fixture["findings"] = []
    if not findings_payload_errors(findings_fixture):
        failures.append("an empty findings ledger must fail closed")

    # ARCHITECTURE.md rule 4. One fixture per branch, and the three that
    # matter are the ones that must never go green: a corrected tree whose
    # manifest does not re-derive, a declared divergence no fidelity report
    # publishes, and a ledger carrying records with no tree built from them.
    # The absent-tree PASS is the only PASS on absence, it now holds ONLY for
    # an empty ledger, and both halves are asserted explicitly so that a later
    # edit widening either is a test change rather than a silent one.
    def _corrected(**over: Any) -> Result:
        fixture: dict[str, Any] = {
            "tree_exists": True, "manifest_exists": True,
            "verify_code": 0, "verify_output": "",
            "divergence_reports": [], "fidelity_text": None,
            "ledger_nonempty": False,
        }
        fixture.update(over)
        return corrected_tree_result(**fixture)

    declared = ["diverges by declared override C001, authorised by RR 11-2018"]
    corrected_cases: tuple[tuple[str, Result, Verdict], ...] = (
        ("an unbuilt stage 2 with an empty ledger is not a skipped check",
         _corrected(tree_exists=False, manifest_exists=False), Verdict.PASS),
        ("a declared record with no corrected tree is a build failure",
         _corrected(tree_exists=False, manifest_exists=False,
                    ledger_nonempty=True), Verdict.FAIL),
        ("a declared record with a manifest but no tree is a build failure",
         _corrected(tree_exists=False, manifest_exists=True,
                    ledger_nonempty=True), Verdict.FAIL),
        ("a corrected tree with no manifest is a build failure",
         _corrected(manifest_exists=False), Verdict.FAIL),
        ("a manifest that does not re-derive is a build failure",
         _corrected(verify_code=1,
                    verify_output="manifest does not re-derive: files[3]"),
         Verdict.FAIL),
        ("a verified manifest declaring nothing passes",
         _corrected(), Verdict.PASS),
        ("a declared divergence with no fidelity report is a build failure",
         _corrected(divergence_reports=declared), Verdict.FAIL),
        ("a declared divergence absent from the report is a build failure",
         _corrected(divergence_reports=declared,
                    fidelity_text="{\"forms\": []}"), Verdict.FAIL),
        ("a declared divergence the report names passes",
         _corrected(divergence_reports=declared,
                    fidelity_text=f"report: {declared[0]}"), Verdict.PASS),
    )
    for label, result, expected in corrected_cases:
        if result.verdict is not expected or result.name != "corrected-tree":
            failures.append(f"corrected-tree rule 4: {label}")
    if "corrected-tree" not in CHECKS:
        failures.append("rule 4 is not wired into the gate's check inventory")

    # The unbuilt-with-records FAIL has to SAY why, because the operator's next
    # move differs entirely from every other corrected-tree failure: build the
    # tree, do not repair one.
    unbuilt_with_records = _corrected(tree_exists=False, manifest_exists=False,
                                      ledger_nonempty=True)
    if "stage 2 ledger has records but forms-corrected/ does not exist" \
            not in unbuilt_with_records.detail:
        failures.append("the unbuilt-with-records failure must name the ledger "
                        "as its reason")
    if "ledger" in _corrected(tree_exists=False, manifest_exists=False).detail:
        failures.append("the empty-ledger unbuilt PASS must stay the plain "
                        "'stage 2 is unbuilt' statement")
    # A tree that EXISTS is judged on itself. Whether the ledger is empty
    # cannot change any of those verdicts, or a record deleted from the ledger
    # would quietly re-colour a real corrected tree.
    for label, over in (
            ("no manifest", {"manifest_exists": False}),
            ("verify fails", {"verify_code": 1}),
            ("nothing declared", {}),
            ("declared, no report", {"divergence_reports": declared}),
            ("declared and named", {"divergence_reports": declared,
                                    "fidelity_text": f"x {declared[0]} y"})):
        with_ledger = _corrected(ledger_nonempty=True, **over)
        without = _corrected(**over)
        if with_ledger.verdict is not without.verdict:
            failures.append(f"a present corrected tree ({label}) must not "
                            f"change verdict with the ledger's contents")
    # The live check must actually consult the ledger. A pure function nothing
    # ever passes `ledger_nonempty` to is a branch that cannot fire, which is
    # how the previous version of this check spent its whole life green while
    # C01 sat declared in the ledger. Driven through the real function with the
    # three paths redirected, so the wiring is what is tested and not a
    # restatement of it.
    global CORRECTIONS_LEDGER, CORRECTED_TREE, CORRECTED_MANIFEST
    saved = (CORRECTIONS_LEDGER, CORRECTED_TREE, CORRECTED_MANIFEST)
    with tempfile.TemporaryDirectory() as scratch:
        root = pathlib.Path(scratch)
        ledger = root / "corrections"
        (ledger / "evidence").mkdir(parents=True)
        (ledger / "evidence" / "C99-evidence.json").write_text("{}", encoding="utf-8")
        (ledger / "README.md").write_text("prose", encoding="utf-8")
        try:
            CORRECTIONS_LEDGER = ledger
            CORRECTED_TREE = root / "forms-corrected"
            CORRECTED_MANIFEST = root / "forms-corrected.manifest.json"
            if ledger_record_names():
                failures.append("prose and nested evidence are not ledger "
                                "records")
            empty = check_corrected_tree()
            if empty.verdict is not Verdict.PASS:
                failures.append("an unbuilt stage 2 with an empty ledger must "
                                f"still pass, got {empty.verdict.value}")
            (ledger / "C99-probe.json").write_text("{}", encoding="utf-8")
            if ledger_record_names() != ["C99-probe.json"]:
                failures.append("a root-level record must be seen by the "
                                "ledger scan")
            declared_only = check_corrected_tree()
            if declared_only.verdict is not Verdict.FAIL:
                failures.append("a declared record with no corrected tree must "
                                f"fail live, got {declared_only.verdict.value}")
            if "C99-probe.json" not in declared_only.detail:
                failures.append("the live failure must name the records it "
                                f"found: {declared_only.detail}")
        finally:
            CORRECTIONS_LEDGER, CORRECTED_TREE, CORRECTED_MANIFEST = saved

    huge_json_integer = ("[" + "9" * 5000 + "]").encode("ascii")
    try:
        huge_json_errors = validate_audit_application_envelope(
            {}, huge_json_integer)
    except Exception as error:  # noqa: BLE001 - hostile JSON probe
        failures.append(
            "huge JSON integers must not escape envelope validation: "
            f"{type(error).__name__}: {error}")
    else:
        if not huge_json_errors:
            failures.append("huge JSON integers must fail closed")

    probe = Result("probe", Verdict.UNEVALUABLE, "x")
    if summarise([probe]) == 0:
        failures.append("an UNEVALUABLE check must make the gate exit non-zero")
    if summarise([Result("probe", Verdict.PASS, "x")]) != 0:
        failures.append("an all-PASS run must exit 0")

    # 8 from GOAL.md + G10's two field-layer assertions. The literal is here so
    # that adding a name to REQUIRED_ASSERTIONS without declaring its count
    # contract and its fixture entry fails the self-test rather than a 60-minute
    # gate run.
    if len(REQUIRED_ASSERTIONS) != 10:
        failures.append(f"GOAL.md names 8 assertions and G10 adds 2, gate has "
                        f"{len(REQUIRED_ASSERTIONS)}")
    if set(REQUIRED_ASSERTIONS) - set(BASIC_ASSERTION_COUNT_FIELDS) != {
            "comb_slots_match_printed"}:
        failures.append("every non-comb required assertion needs a declared "
                        "count contract")
    if "comb_referee" not in SELF_TEST_MODULES:
        failures.append("comb_referee.py must be included in module self-tests")
    if "field_identity" not in SELF_TEST_MODULES:
        failures.append("field_identity.py must be included in module self-tests")
    if "map_tin" not in SELF_TEST_MODULES:
        failures.append("map_tin.py must be included in module self-tests")
    if EXPECTED_UNCATALOGUED_FILLABLES != 0:
        failures.append("I3 coverage pin EXPECTED_UNCATALOGUED_FILLABLES must be 0")
    if EXPECTED_FILLABLE_CELLS != 9990:
        failures.append("I3 fillable census pin moved without its catalog/self-test twin")

    # Built from the census constants rather than repeating 38 and 13 as
    # literals: the fixture and the thing it certifies must move together, and
    # they did not -- the corpus grew to 38/15 while this fixture still built
    # 38/13 and failed as though the tree were wrong.
    direct_paths = [
        f"forms/direct-{index}/provenance.json"
        for index in range(EXPECTED_IN_CORPUS_FORMS)]
    extra_paths = [
        f"forms/extra/extra-{index}/provenance.json"
        for index in range(EXPECTED_EXTRA_FORMS)]
    corpus_fixture = direct_paths + extra_paths + [
        "forms/direct-0/unrelated\nname.json"]
    try:
        fixture_slugs = _canonical_form_slugs_from_paths(corpus_fixture)
        if (len(fixture_slugs) != EXPECTED_FORMS
                or "direct-0" not in fixture_slugs
                or f"extra-{EXPECTED_EXTRA_FORMS - 1}" not in fixture_slugs):
            failures.append(
                f"the canonical corpus must include {EXPECTED_IN_CORPUS_FORMS} "
                f"direct and {EXPECTED_EXTRA_FORMS} extra forms")
    except Exception as error:  # noqa: BLE001 - self-test reports exact failure
        failures.append(f"valid canonical corpus fixture failed: {error}")
    for label, paths in (
            ("duplicate", corpus_fixture + [
                "forms/extra/direct-0/provenance.json"]),
            ("missing", direct_paths[:-1] + extra_paths),
            ("extra", direct_paths + extra_paths + [
                "forms/direct-new/provenance.json"]),
            ("wrong root distribution", direct_paths + extra_paths[:-1] + [
                "forms/moved-extra/provenance.json"]),
            ("nested", corpus_fixture + [
                "forms/direct-0/nested/provenance.json"]),
            ("unsupported namespace", corpus_fixture + [
                "forms/archive/form/provenance.json"]),
            ("reserved extra root", corpus_fixture + [
                "forms/extra/provenance.json"]),
            ):
        try:
            _canonical_form_slugs_from_paths(paths)
        except CombRefereeScopeError:
            pass
        else:
            failures.append(
                f"canonical corpus must reject {label} provenance evidence")
    try:
        tracked_inventory = canonical_form_inventory()
        if len(tracked_inventory) != EXPECTED_FORMS:
            failures.append(
                f"the tracked canonical corpus must resolve {EXPECTED_FORMS} forms")
        batch_fixture = []
        for slug, in_corpus in tracked_inventory.items():
            record = _synthetic_batch_record(slug)
            record["in_corpus"] = in_corpus
            batch_fixture.append(record)
        if batch_report_errors(
                batch_fixture, frozenset(tracked_inventory),
                tracked_inventory):
            failures.append(
                "a correctly root-classified batch corpus must validate")
        flipped_batch = json.loads(json.dumps(batch_fixture))
        flipped_batch[0]["in_corpus"] = not flipped_batch[0]["in_corpus"]
        if not batch_report_errors(
                flipped_batch, frozenset(tracked_inventory),
                tracked_inventory):
            failures.append(
                "a flipped batch in_corpus classification must fail closed")
    except Exception as error:  # noqa: BLE001
        failures.append(f"tracked canonical corpus failed: {error}")

    try:
        import py_compile
        with tempfile.TemporaryDirectory(
                prefix=".gate-isolation-self-test-") as temporary:
            root = pathlib.Path(temporary)
            module_root = root / "module"
            shadow_root = root / "shadow"
            module_root.mkdir()
            shadow_root.mkdir()
            marker = root / "sitecustomize-ran"
            (shadow_root / "sitecustomize.py").write_text(
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('forged')\n",
                encoding="utf-8")
            module = module_root / "cache_probe.py"
            module.write_text("VALUE = 'forged'\n", encoding="utf-8")
            py_compile.compile(str(module), doraise=True)
            compiled_stat = module.stat()
            # Same size and timestamp make the repository-style pyc look
            # current.  The isolated prefix must nevertheless load source.
            module.write_text("VALUE = 'source'\n", encoding="utf-8")
            os.utime(module, ns=(
                compiled_stat.st_atime_ns, compiled_stat.st_mtime_ns))
            grandchild_probe = (
                "import json,sys;import fitz;"
                "print(json.dumps({"
                "'isolated':sys.flags.isolated,"
                "'no_site':sys.flags.no_site,"
                "'site_loaded':'site' in sys.modules,"
                "'pymupdf':bool(fitz.VersionBind)}))"
            )
            inert_bootstrap_bypass = (
                "import os,time;\n"
                "try: os.setsid()\n"
                "except RuntimeError: print('BLOCKED')\n"
                "else: print('DETACHED',flush=True); time.sleep(2)\n"
            )
            probe = (
                "import json,os,subprocess,sys;"
                f"sys.path.insert(0,{str(module_root)!r});"
                "import cache_probe;"
                "child=subprocess.run([sys.executable,'-c',"
                f"{grandchild_probe!r}],capture_output=True,text=True);"
                "detached=subprocess.run([sys.executable,'-c',"
                "'import json,os;print(json.dumps([os.getpgrp(),os.getsid(0)]))'],"
                "capture_output=True,text=True,start_new_session=True);"
                "bootstrap=str(__import__('pathlib').Path(sys.pycache_prefix).parent/"
                "'bootstrap.py');"
                "argv_bypass=subprocess.run([sys.executable,'-c',"
                f"{inert_bootstrap_bypass!r},bootstrap],"
                "capture_output=True,text=True);"
                "path_env=dict(os.environ);"
                "path_env['PATH']=str(__import__('pathlib').Path(sys.executable).parent);"
                "path_bypass=subprocess.run([__import__('pathlib').Path("
                "sys.executable).name,'-c',"
                f"{inert_bootstrap_bypass!r}],"
                "capture_output=True,text=True,env=path_env);"
                "blocked=[];"
                "\ntry: os.posix_spawn('/bin/sleep',['sleep','20'],os.environ,setsid=True)"
                "\nexcept RuntimeError: blocked.append('posix_spawn')"
                "\ntry: os.fork()"
                "\nexcept RuntimeError: blocked.append('fork')"
                "\ntry: os._exit(0)"
                "\nexcept RuntimeError: blocked.append('_exit')"
                "\ntry: subprocess.Popen(['/bin/echo','unsafe'],0)"
                "\nexcept RuntimeError: blocked.append('positional_popen')"
                "\ntry: subprocess.Popen(['not-python'],executable=sys.executable)"
                "\nexcept RuntimeError: blocked.append('executable_override')"
                "\ntry: subprocess.Popen('echo unsafe',shell=True)"
                "\nexcept RuntimeError: blocked.append('shell')"
                "\n"
                "print(json.dumps({"
                "'value':cache_probe.VALUE,"
                "'isolated':sys.flags.isolated,"
                "'no_site':sys.flags.no_site,"
                "'site_loaded':'site' in sys.modules,"
                "'dont_write':sys.dont_write_bytecode,"
                "'pycache_prefix':bool(sys.pycache_prefix),"
                "'argv':sys.argv[1:],"
                "'cwd':os.getcwd(),"
                "'child_code':child.returncode,"
                "'child':json.loads(child.stdout),"
                "'detached_code':detached.returncode,"
                "'detached_same_group':json.loads(detached.stdout)=="
                "[os.getpgrp(),os.getsid(0)],"
                "'argv_bypass':argv_bypass.stdout.strip(),"
                "'path_bypass':path_bypass.stdout.strip(),"
                "'blocked':blocked}))"
            )
            hostile_environment = dict(os.environ)
            hostile_environment["PYTHONPATH"] = str(shadow_root)
            hostile_environment["PYTHONHOME"] = str(shadow_root)
            probe_argv = ["-c", probe, "sentinel"]
            execution = run_isolated_python_attested(
                probe_argv, 30, hostile_environment)
            code, output = execution.code, execution.output
            lines = [line for line in output.splitlines() if line.strip()]
            isolation = json.loads(lines[-1]) if code == 0 and lines else {}
            expected_isolation = {
                    "value": "source",
                    "isolated": 1,
                    "no_site": 1,
                    "site_loaded": False,
                    "dont_write": True,
                    "pycache_prefix": True,
                    "argv": ["sentinel"],
                    "cwd": str(REPO.resolve()),
                    "child_code": 0,
                    "child": {
                        "isolated": 1,
                        "no_site": 1,
                        "site_loaded": False,
                        "pymupdf": True,
                    },
                    "detached_code": 0,
                    "detached_same_group": True,
                    "argv_bypass": "BLOCKED",
                    "path_bypass": "BLOCKED",
                    "blocked": [
                        "posix_spawn", "fork", "_exit",
                        "positional_popen", "executable_override", "shell"],
                    }
            if isolation != expected_isolation or marker.exists():
                # Name what diverged. This probe first failed on a hosted Linux
                # runner where nobody could reproduce it interactively, and a
                # bare name gave the reader nothing: which field, expected what,
                # observed what. A failure that cannot be acted on from its own
                # text is this project's oldest defect, and it was here too.
                divergent = sorted(
                    key for key in set(expected_isolation) | set(isolation)
                    if isolation.get(key) != expected_isolation.get(key))
                detail = "; ".join(
                    f"{key}: expected {expected_isolation.get(key)!r}, "
                    f"observed {isolation.get(key)!r}" for key in divergent)
                if marker.exists():
                    detail = (detail + "; " if detail else "") + \
                        "inherited sitecustomize RAN (marker file exists)"
                if code != 0:
                    detail = (f"probe exited {code}; last output: "
                              f"{output.strip().splitlines()[-3:]}") + \
                        (f" -- {detail}" if detail else "")
                failures.append(
                    "isolated Python child must ignore inherited sitecustomize "
                    "and repository pyc, preserve argv/cwd, and bind "
                    f"descendants -- {detail}")
            receipt_errors = (["receipt is None"] if execution.receipt is None
                              else isolated_launch_receipt_errors(
                                  execution.receipt,
                                  execution.receipt.get("dependency_manifest"),
                                  code,
                                  str(pathlib.Path(sys.executable).resolve()),
                                  probe_argv))
            if receipt_errors:
                failures.append(
                    "isolated Python execution must publish a valid v2 receipt "
                    f"-- {'; '.join(str(e) for e in receipt_errors[:4])}")

            dependency_source = root / "dependency.py"
            dependency_source.write_text("VALUE = 1\n", encoding="utf-8")
            dependency_view = root / "dependency-view"
            dependency_view.mkdir()
            shutil.copy2(
                dependency_source, dependency_view / dependency_source.name)
            copied_dependency = dependency_view / dependency_source.name
            copied_dependency.chmod(
                stat.S_IMODE(copied_dependency.stat().st_mode) & ~0o222)
            byte_count, digest = _hash_stable_file(dependency_source)
            dependency_fixture = [{
                "name": dependency_source.name,
                "root": str(dependency_source.parent.resolve()),
                "kind": "file",
                "files": [{
                    "path": "", "type": "file",
                    "mode": stat.S_IMODE(dependency_source.stat().st_mode),
                    "bytes": byte_count, "sha256": digest,
                }],
            }]
            if _validate_isolated_dependencies(
                    dependency_fixture, dependency_view):
                failures.append(
                    "a complete isolated dependency view must validate")
            (dependency_view / dependency_source.name).unlink()
            if not _validate_isolated_dependencies(
                    dependency_fixture, dependency_view):
                failures.append(
                    "a missing isolated dependency link must fail closed")

            fd_root = root / "fd-source"
            package = fd_root / "package"
            (package / "bin").mkdir(parents=True)
            executable = package / "bin" / "tool"
            executable.write_bytes(b"verified executable\n")
            executable.chmod(0o755)
            os.symlink("bin/tool", package / "tool-link")

            def fd_entry() -> dict[str, Any]:
                return {
                    "name": package.name,
                    "root": str(fd_root.resolve()),
                    "kind": "directory",
                    "files": list(_dependency_tree_records(
                        package, "directory").values()),
                }

            original_clone = globals()["_clone_dependency_fd"]
            fallback_view = root / "fd-fallback-view"
            fallback_view.mkdir()
            try:
                globals()["_clone_dependency_fd"] = (
                    lambda _source, _parent, _name: False)
                fallback_entry = fd_entry()
                _materialize_isolated_dependencies(
                    [fallback_entry], fallback_view)
            finally:
                globals()["_clone_dependency_fd"] = original_clone
            if (_validate_isolated_dependencies(
                    [fallback_entry], fallback_view)
                    or os.readlink(
                        fallback_view / "package" / "tool-link")
                    != "bin/tool"
                    or stat.S_IMODE((
                        fallback_view / "package" / "bin" / "tool"
                    ).stat().st_mode) != 0o555):
                failures.append(
                    "same-FD fallback must preserve bytes, executable mode, "
                    "and internal symlinks")

            initial_mode_manifest = _isolated_dependency_manifest([fd_entry()])
            executable.chmod(0o744)
            changed_mode_manifest = _isolated_dependency_manifest([fd_entry()])
            executable.chmod(0o755)
            if (initial_mode_manifest["sha256"]
                    == changed_mode_manifest["sha256"]):
                failures.append(
                    "isolated dependency digest must bind executable modes")

            os.symlink("../missing", package / "dangling")
            try:
                _dependency_tree_records(package, "directory")
            except RuntimeError:
                pass
            else:
                failures.append(
                    "isolated dependencies must reject dangling symlinks")
            (package / "dangling").unlink()

            swap_entry = fd_entry()
            original_package = fd_root / "package-original"
            package.rename(original_package)
            os.symlink(original_package.name, package)
            swap_view = root / "fd-swap-view"
            swap_view.mkdir()
            try:
                _materialize_isolated_dependencies([swap_entry], swap_view)
            except (OSError, RuntimeError):
                pass
            else:
                failures.append(
                    "a source path swapped to a symlink must fail closed")
            package.unlink()
            original_package.rename(package)

            corrupt_view = root / "fd-corrupt-clone-view"
            corrupt_view.mkdir()

            def corrupt_clone(
                    _source: int, destination_parent: int,
                    name: str) -> bool:
                descriptor = os.open(
                    name, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600, dir_fd=destination_parent)
                try:
                    os.write(descriptor, b"corrupt")
                finally:
                    os.close(descriptor)
                return True

            try:
                globals()["_clone_dependency_fd"] = corrupt_clone
                try:
                    _materialize_isolated_dependencies(
                        [dependency_fixture[0]], corrupt_view)
                except RuntimeError:
                    pass
                else:
                    failures.append(
                        "a corrupt successful clone must fail destination "
                        "verification")
            finally:
                globals()["_clone_dependency_fd"] = original_clone

            mutate_view = root / "fd-mutated-source-view"
            mutate_view.mkdir()
            original_copy = globals()["_copy_fd_payload"]
            original_source_mode = stat.S_IMODE(
                dependency_source.stat().st_mode)

            def mutate_during_copy(
                    source_descriptor: int,
                    destination_descriptor: int) -> None:
                original_copy(source_descriptor, destination_descriptor)
                os.fchmod(source_descriptor, original_source_mode ^ 0o100)

            try:
                globals()["_clone_dependency_fd"] = (
                    lambda _source, _parent, _name: False)
                globals()["_copy_fd_payload"] = mutate_during_copy
                try:
                    _materialize_isolated_dependencies(
                        [dependency_fixture[0]], mutate_view)
                except RuntimeError:
                    pass
                else:
                    failures.append(
                        "a source inode mutated during same-FD fallback must "
                        "fail closed")
            finally:
                globals()["_clone_dependency_fd"] = original_clone
                globals()["_copy_fd_payload"] = original_copy
                dependency_source.chmod(original_source_mode)

            timeout_started = time.monotonic()
            timeout_execution = run_isolated_python_attested(
                ["-c", "import time; time.sleep(60)"], 1)
            timeout_elapsed = time.monotonic() - timeout_started
            timeout_receipt = timeout_execution.receipt or {}
            timeout_budget = 1 + COMB_REFEREE_CLEANUP_TIMEOUT_SECONDS + 10
            if (timeout_execution.code != 124
                    or timeout_receipt.get("timed_out") is not True
                    or timeout_receipt.get("cleanup_complete") is not True
                    or timeout_elapsed > timeout_budget):
                failures.append(
                    "isolated Python hard timeout must clean up within its "
                    f"bounded allowance -- code={timeout_execution.code} "
                    f"(want 124), timed_out="
                    f"{timeout_receipt.get('timed_out')!r}, cleanup_complete="
                    f"{timeout_receipt.get('cleanup_complete')!r}, elapsed "
                    f"{timeout_elapsed:.1f}s of {timeout_budget:.0f}s allowed")

            lingering_execution = run_isolated_python_attested([
                "-c",
                "import subprocess;"
                "child=subprocess.Popen(['/bin/sleep','60']);"
                "print('LINGERING',child.pid,flush=True)",
            ], 30)
            lingering_match = re.search(
                r"^LINGERING ([0-9]+)$",
                lingering_execution.output, re.MULTILINE)
            lingering_alive = False
            if lingering_match is not None:
                try:
                    os.kill(int(lingering_match.group(1)), 0)
                except ProcessLookupError:
                    pass
                else:
                    lingering_alive = True
            if (lingering_execution.code == 0
                    or lingering_match is None
                    or lingering_alive
                    or lingering_execution.receipt is not None):
                failures.append(
                    "a descendant that outlives the root must be killed and "
                    "must invalidate the success receipt -- "
                    f"code={lingering_execution.code} (want nonzero), "
                    f"marker_seen={lingering_match is not None}, "
                    f"descendant_alive={lingering_alive}, "
                    f"receipt_suppressed={lingering_execution.receipt is None}; "
                    f"output tail: {lingering_execution.output.strip().splitlines()[-2:]}")
    except Exception as error:  # noqa: BLE001 - self-test must report failure
        failures.append(
            "isolated Python child probe failed: "
            f"{type(error).__name__}: {error}")

    clone = lambda value: json.loads(json.dumps(value))  # noqa: E731

    try:
        with tempfile.TemporaryDirectory(
                prefix=".gate-metric-self-test-") as temporary:
            metric_path = pathlib.Path(temporary) / "audit.json"
            original_audit_path = AUDIT_JSON
            globals()["AUDIT_JSON"] = metric_path
            slugs = sorted(canonical_form_slugs())
            valid_records = [_synthetic_audit_record(slug) for slug in slugs]

            def publish_metric_fixture(records: Any) -> None:
                metric_path.write_text(
                    json.dumps(records, sort_keys=True) + "\n",
                    encoding="utf-8")

            publish_metric_fixture(valid_records)
            for result in (
                    check_rules(), check_paper(), check_artwork(), check_text(),
                    check_assertions()):
                if result.verdict is not Verdict.PASS:
                    failures.append(
                        f"complete metric fixture must pass {result.name}: "
                        f"{result.detail}")

            metric_fields = {
                "measured", "paper_ok", "rules_ref", "rules_missing", "rules_extra",
                "rules_thickness_violations", "rules_pct", "images_missing",
                "images_placement_violations", "text_missing", "text_extra",
                "text_ref", "text_pct",
            }
            assertion_only = clone(valid_records)
            for record in assertion_only:
                for key in metric_fields:
                    record.pop(key, None)
            publish_metric_fixture(assertion_only)
            for checker in (
                    check_rules, check_paper, check_artwork, check_text):
                result = checker()
                if result.verdict is not Verdict.UNEVALUABLE:
                    failures.append(
                        f"assertion-only audit must make {result.name} "
                        "UNEVALUABLE")
            if check_assertions().verdict is not Verdict.PASS:
                failures.append(
                    "assertion-only audit must remain usable for assertions")

            assertion_key = "inputs_over_printed_text"
            missing_assertion = clone(assertion_only)
            missing_assertion[0].pop(assertion_key)
            publish_metric_fixture(missing_assertion)
            if check_assertions().verdict is not Verdict.UNEVALUABLE:
                failures.append(
                    "a missing per-form assertion must be UNEVALUABLE")
            malformed_assertion = clone(assertion_only)
            malformed_assertion[0][assertion_key] = None
            publish_metric_fixture(malformed_assertion)
            if check_assertions().verdict is not Verdict.UNEVALUABLE:
                failures.append(
                    "a malformed per-form assertion must be UNEVALUABLE")
            failed_assertion = clone(assertion_only)
            failed_assertion[0][assertion_key] = False
            failed_assertion[0]["assertions"][assertion_key].update({
                "holds": False,
                "reason": "synthetic assertion failure",
                "offender_count": 0,
                "offenders_published": 0,
                "offenders_omitted": 0,
                "offenders_complete": True,
            })
            failed_assertion[0]["assertions_held"] -= 1
            publish_metric_fixture(failed_assertion)
            if check_assertions().verdict is not Verdict.FAIL:
                failures.append(
                    "a genuine false assertion must be FAIL")

            malformed_values = (
                ("measured", check_rules, [None, False, 0, 1, "true"]),
                ("rules_missing", check_rules,
                 [None, True, 1.5, "0", -1]),
                ("rules_ref", check_rules,
                 [None, True, 0, 1.5, "1", -1]),
                ("rules_pct", check_rules,
                 [None, float("nan"), float("inf"), float("-inf"),
                  True, "100", -1, 101, 10 ** 1000]),
                ("paper_ok", check_paper, [None, 0, 1, "true"]),
                ("images_missing", check_artwork,
                 [None, True, 1.5, "0", -1]),
                ("text_pct", check_text,
                 [None, float("nan"), float("inf"), True, "100", -1, 101]),
                ("text_ref", check_text,
                 [None, True, 0, 1.5, "1", -1]),
            )
            for key, checker, invalid_values in malformed_values:
                missing_field = clone(valid_records)
                missing_field[0].pop(key)
                publish_metric_fixture(missing_field)
                if checker().verdict is not Verdict.UNEVALUABLE:
                    failures.append(
                        f"missing metric field must make {key} UNEVALUABLE")
                for invalid in invalid_values:
                    malformed = clone(valid_records)
                    malformed[0][key] = invalid
                    publish_metric_fixture(malformed)
                    if checker().verdict is not Verdict.UNEVALUABLE:
                        failures.append(
                            f"malformed metric field must make {key} "
                            f"UNEVALUABLE: {invalid!r}")

            for key, checker in (
                    ("rules_missing", check_rules),
                    ("images_missing", check_artwork),
                    ("text_extra", check_text)):
                genuine_failure = clone(valid_records)
                genuine_failure[0][key] = 1
                if key == "rules_missing":
                    genuine_failure[0]["rules_pct"] = 0.0
                publish_metric_fixture(genuine_failure)
                if checker().verdict is not Verdict.FAIL:
                    failures.append(
                        f"valid nonzero {key} must be a metric failure")
            for key, checker, value in (
                    ("rules_pct", check_rules, 99.0),
                    ("text_pct", check_text, 99.0),
                    ("paper_ok", check_paper, False)):
                genuine_failure = clone(valid_records)
                genuine_failure[0][key] = value
                if key == "rules_pct":
                    genuine_failure[0].update({
                        "rules_ref": 100, "rules_missing": 1})
                elif key == "text_pct":
                    genuine_failure[0].update({
                        "text_ref": 100, "text_missing": 1})
                publish_metric_fixture(genuine_failure)
                if checker().verdict is not Verdict.FAIL:
                    failures.append(
                        f"valid failing {key} must be a metric failure")

            inconsistent_percentage = clone(valid_records)
            inconsistent_percentage[0]["rules_missing"] = 1
            publish_metric_fixture(inconsistent_percentage)
            if check_rules().verdict is not Verdict.UNEVALUABLE:
                failures.append(
                    "a percentage detached from its denominator must be "
                    "UNEVALUABLE")

            producer_rounding_boundary = clone(valid_records)
            producer_rounding_boundary[0].update({
                "rules_ref": 4000,
                "rules_missing": 1,
                # This is audit.py's binary-float round(..., 2) result.
                "rules_pct": 99.97,
            })
            publish_metric_fixture(producer_rounding_boundary)
            if _percentage_evidence_errors(
                    producer_rounding_boundary[0], "rules_pct",
                    "producer-rounding-fixture"):
                failures.append(
                    "producer percentage rounding boundary must remain "
                    "evaluable")

            identity_fixtures = []
            missing_form = clone(valid_records[:-1])
            identity_fixtures.append(("missing", missing_form))
            extra_form = clone(valid_records)
            extra_form.append(_synthetic_audit_record("not-in-corpus"))
            identity_fixtures.append(("extra", extra_form))
            duplicate_form = clone(valid_records)
            duplicate_form[-1]["slug"] = duplicate_form[0]["slug"]
            identity_fixtures.append(("duplicate", duplicate_form))
            substituted_form = clone(valid_records)
            substituted_form[-1]["slug"] = "not-in-corpus"
            identity_fixtures.append(("substituted", substituted_form))
            non_object = clone(valid_records)
            non_object[-1] = "not-an-object"
            identity_fixtures.append(("non-object", non_object))
            for label, records in identity_fixtures:
                publish_metric_fixture(records)
                if (check_rules().verdict is not Verdict.UNEVALUABLE
                        or check_assertions().verdict is not Verdict.UNEVALUABLE):
                    failures.append(
                        f"{label} audit corpus identity must fail closed")
    except Exception as error:  # noqa: BLE001 - self-test names the breakage
        failures.append(
            "metric evidence self-test failed: "
            f"{type(error).__name__}: {error}")
    finally:
        if "original_audit_path" in locals():
            globals()["AUDIT_JSON"] = original_audit_path

    report, snapshot, raw_payload, envelope = _synthetic_comb_fixture()
    report_errors, report_stats = validate_comb_referee_report(
        report, child_exit=2, expected_forms=1, expected_subjects=1)
    report_errors.extend(report_binding_errors(report, snapshot, report_stats))
    if report_errors or report_stats["pending_transitions"] != 0:
        failures.append(
            "a complete synthetic comb-referee report must validate: "
            + "; ".join(report_errors[:3]))

    # Real source bands can contain a shorter minority topology below a richer
    # strict-majority topology.  Every layout anchor belongs to the chosen
    # topology; a dominated alternative is intentionally only a proper subset.
    dominated_subset_cell = clone(report["forms"][0]["cells"][0])
    dominated_subset_cell.update({
        "latticed": 3,
        "lattice_divider_x": [3.0, 7.0],
    })
    dominated_subset_referee = clone(dominated_subset_cell["referee"])
    dominated_subset_referee.update({
        "reason": (
            "one richer source topology contains every other slab and "
            "occupies a strict majority of the comb band"),
        "y1": 6.0,
        "source_divider_x": [3.0, 7.0],
        "source_rail_x": [0.0, 10.0],
        "extra_divider_x": [],
        "compartments": 3,
        "anchor_matches": [
            {"layout_x": 3.0, "source_x": 3.0, "delta_pt": 0.0},
            {"layout_x": 7.0, "source_x": 7.0, "delta_pt": 0.0},
        ],
        "components": [
            {
                "x": 3.0, "x0": 2.9, "x1": 3.1, "tone": 0.0,
                "elements": ["fixture-left-divider"], "clipped": False,
            },
            {
                "x": 7.0, "x0": 6.9, "x1": 7.1, "tone": 0.0,
                "elements": ["fixture-right-divider"], "clipped": False,
            },
        ],
        "topology_coverage_pt": {
            "3.0": 4.0,
            "3.0,7.0": 6.0,
        },
        "chosen_topology": [3.0, 7.0],
        "topology_superset_relations": [
            {
                "candidate": [3.0],
                "other": [3.0, 7.0],
                "contains": False,
                "proper": False,
            },
            {
                "candidate": [3.0, 7.0],
                "other": [3.0],
                "contains": True,
                "proper": True,
            },
        ],
    })
    dominated_subset_cell["referee"] = dominated_subset_referee
    subset_errors = _measured_referee_certificate_errors(
        "real-subset-shape", dominated_subset_cell,
        dominated_subset_referee)
    if subset_errors:
        failures.append(
            "a dominated minority source topology must remain evaluable: "
            + "; ".join(subset_errors[:3]))
    forged_subset_relations = clone(dominated_subset_referee)
    forged_subset_relations["topology_superset_relations"][1][
        "proper"] = False
    if not _measured_referee_certificate_errors(
            "forged-subset-shape", dominated_subset_cell,
            forged_subset_relations):
        failures.append(
            "forged topology-superset evidence must still fail closed")

    # The referee's third measured-source shape: the fail-closed
    # partial-anchor certificate.  An active_unresolved subject proves a
    # lattice anchor ABSENT because Poppler shows the raw rail exhaustively
    # erased by one supported, unclipped, non-target final owner across the
    # whole open band.  The gate re-derives that proof; it never accepts the
    # certificate's own valid flag.
    partial_anchor_cell = clone(report["forms"][0]["cells"][0])
    partial_anchor_cell.update({
        "ledger_state": "active_unresolved",
        "latticed": 3,
        "lattice_divider_x": [3.0, 7.0],
    })
    partial_anchor_referee = {
        "status": "measured",
        "reason": REFEREE_PARTIAL_ANCHOR_REASON,
        "y0": 0.0,
        "y1": 10.0,
        "source_divider_x": [3.0],
        "source_rail_x": [0.0, 10.0],
        "rail_derivation": {
            "left": {"basis": "owner-edge"},
            "right": {"basis": "owner-edge"},
        },
        "extra_divider_x": [],
        "compartments": 2,
        "anchor_matches": [{
            "layout_x": 3.0, "source_x": 3.0, "delta_pt": 0.0,
        }],
        "positions_match": False,
        "anchors_complete": False,
        "missing_anchor_x": [7.0],
        "components": [{
            "x": 3.0, "x0": 2.9, "x1": 3.1, "tone": 0.0,
            "elements": ["fixture-divider"], "clipped": False,
        }],
        "contract_y0": 0.0,
        "contract_y1": 10.0,
        "open_y0": 0.0,
        "open_y1": 10.0,
        "contract_span_pt": 10.0,
        "seed_span_pt": 10.0,
        "measured_span_pt": 10.0,
        "unmeasured_span_pt": 0.0,
        "topology_coverage_pt": {"3.0": 10.0},
        "ignored_slabs": [],
        "chosen_topology": [3.0],
        "topology_superset_relations": [],
        "active_partial_anchor_certificate": {
            "criterion": PARTIAL_ANCHOR_CRITERION,
            "valid": True,
            "ledger_state": "active_unresolved",
            "subject_ownership_basis": PARTIAL_ANCHOR_OWNERSHIP_BASIS,
            "independent_source_enclosure_proven": False,
            "divider_count_basis": PARTIAL_ANCHOR_COUNT_BASIS,
            "missing_anchor_basis": PARTIAL_ANCHOR_MISSING_BASIS,
            "anchor_corridor_clipped_paint_elements": [],
            "anchor_corridor_unsupported_region_elements": [],
            "open_y0": 0.0,
            "open_y1": 10.0,
            "coverage_pt": 10.0,
            "source_divider_x": [3.0],
            "observed_anchor_x": [3.0],
            "missing_anchor_x": [7.0],
            "missing_anchor_proofs": [{
                "layout_x": 7.0,
                "corridor_x0": 6.75,
                "corridor_x1": 7.25,
                "proof_x0": 6.75,
                "proof_x1": 7.25,
                "open_y0": 0.0,
                "open_y1": 10.0,
                "raw_anchor_rails": [{
                    "element": "fixture-erased-rail",
                    "order": 1,
                    "kind": "stroke",
                    "x0": 6.9,
                    "x1": 7.1,
                    "center_x": 7.0,
                    "delta_pt": 0.0,
                    "y0": 0.0,
                    "y1": 10.0,
                    "tone": 0.0,
                    "clipped": False,
                }],
                "raw_rail_identity_valid": True,
                "proof_top_role_ambiguities": [],
                "erasure_slabs": [{
                    "y0": 0.0,
                    "y1": 10.0,
                    "sample_y": 5.0,
                    "raw_rail_elements": ["fixture-erased-rail"],
                    "raw_intervals": [[6.9, 7.1]],
                    "final_owner_segments": [{
                        "x0": 6.9,
                        "x1": 7.1,
                        "element": "fixture-white-erasure",
                        "order": 2,
                        "kind": "fill",
                        "tone": 1.0,
                        "clipped": False,
                    }],
                    "ambiguous_top_roles": [],
                }],
                "erasure_owner_roles": [{
                    "element": "fixture-white-erasure",
                    "order": 2,
                    "kind": "fill",
                    "tone": 1.0,
                }],
                "clipped_paint_elements": [],
                "final_target_tone_segments": [],
                "unsupported_region_elements": [],
            }],
        },
    }
    partial_anchor_cell["referee"] = partial_anchor_referee
    partial_accept_errors = _measured_referee_certificate_errors(
        "partial-anchor-shape", partial_anchor_cell, partial_anchor_referee)
    if partial_accept_errors:
        failures.append(
            "a proven partial-anchor certificate must be evaluable: "
            + "; ".join(partial_accept_errors[:3]))

    def partial_anchor_guard(
            mutator: Callable[[dict[str, Any], dict[str, Any]], None],
            ) -> list[str]:
        mutated_cell = clone(partial_anchor_cell)
        mutator(mutated_cell, mutated_cell["referee"])
        return _measured_referee_certificate_errors(
            "partial-anchor-guard", mutated_cell, mutated_cell["referee"])

    def _first_proof(referee_value: dict[str, Any]) -> dict[str, Any]:
        return referee_value["active_partial_anchor_certificate"][
            "missing_anchor_proofs"][0]

    def uncovered_rail(_cell: dict[str, Any],
                       referee_value: dict[str, Any]) -> None:
        # The SVG shows the final owner stopping short of the rail: an
        # uncovered sliver of target-tone rail still reaches paper.
        _first_proof(referee_value)["erasure_slabs"][0][
            "final_owner_segments"][0]["x1"] = 7.0

    def target_tone_owner(_cell: dict[str, Any],
                          referee_value: dict[str, Any]) -> None:
        # The final owner is itself target tone: nothing was erased.
        proof = _first_proof(referee_value)
        proof["erasure_slabs"][0]["final_owner_segments"][0]["tone"] = 0.0
        proof["erasure_owner_roles"][0]["tone"] = 0.0

    def clipped_owner(_cell: dict[str, Any],
                      referee_value: dict[str, Any]) -> None:
        _first_proof(referee_value)["erasure_slabs"][0][
            "final_owner_segments"][0]["clipped"] = True

    def underpainted_owner(_cell: dict[str, Any],
                           referee_value: dict[str, Any]) -> None:
        # An owner painted before the rail cannot be the final owner.
        proof = _first_proof(referee_value)
        proof["erasure_slabs"][0]["final_owner_segments"][0]["order"] = 1
        proof["erasure_owner_roles"][0]["order"] = 1

    def short_rail(_cell: dict[str, Any],
                   referee_value: dict[str, Any]) -> None:
        # The raw rail does not span the open band, so full-band absence
        # was never shown.
        _first_proof(referee_value)["raw_anchor_rails"][0]["y1"] = 6.0

    def short_slab(_cell: dict[str, Any],
                   referee_value: dict[str, Any]) -> None:
        # The erasure slabs leave part of the band without evidence.
        _first_proof(referee_value)["erasure_slabs"][0]["y1"] = 6.0

    def partial_band_coverage(_cell: dict[str, Any],
                              referee_value: dict[str, Any]) -> None:
        # A minority band cannot prove absence, however self-consistent.
        referee_value.update({
            "y1": 6.0,
            "measured_span_pt": 6.0,
            "unmeasured_span_pt": 4.0,
            "topology_coverage_pt": {"3.0": 6.0},
        })
        referee_value["active_partial_anchor_certificate"][
            "coverage_pt"] = 6.0

    def ineligible_ledger(cell_value: dict[str, Any],
                          _referee_value: dict[str, Any]) -> None:
        cell_value["ledger_state"] = "active_resolved"

    def forged_position_verdict(_cell: dict[str, Any],
                                referee_value: dict[str, Any]) -> None:
        # The referee holds positions_match False deliberately for this
        # kind: a declared anchor with no source position cannot match.
        referee_value["positions_match"] = True

    def no_missing_anchor(_cell: dict[str, Any],
                          referee_value: dict[str, Any]) -> None:
        referee_value["missing_anchor_x"] = []
        referee_value["active_partial_anchor_certificate"].update({
            "missing_anchor_x": [], "missing_anchor_proofs": [],
        })

    for guard_label, guard in (
            ("an erasure the SVG does not support (uncovered rail)",
             uncovered_rail),
            ("a target-tone final owner", target_tone_owner),
            ("a clipped final owner", clipped_owner),
            ("an owner painted before the rail", underpainted_owner),
            ("a raw rail short of the open band", short_rail),
            ("erasure slabs short of the open band", short_slab),
            ("partial band coverage", partial_band_coverage),
            ("an ineligible ledger state", ineligible_ledger),
            ("a forged position verdict", forged_position_verdict),
            ("an empty missing-anchor inventory", no_missing_anchor)):
        if not partial_anchor_guard(guard):
            failures.append(
                f"a partial-anchor certificate with {guard_label} "
                "must fail closed")

    # The same shape must also flow through the report totals: a
    # partial-anchor cell disagrees with its lattice count, so the form and
    # report mismatch totals recompute to 1/1 without any schema error.
    partial_report = clone(report)
    partial_form = partial_report["forms"][0]
    report_partial_cell = clone(partial_anchor_cell)
    report_partial_cell.update({
        "ledger_blocks_gate": True,
        "ledger_reason_codes": ["fixture-final-count-regression"],
        "emitted": 3,
        "audit_printed": 3,
        "audit_relation": "complete-non-offender",
        "comparison_status": "stop",
        "comparison_reason": "referee positions disagree with lattice anchors",
        "transition_status": "blocked",
        "transition_reason": (
            "active unresolved ledger subject remains blocking while "
            "comparison status is stop"),
        "four_way": {
            "referee": 2, "lattice": 3, "audit": 3, "emitted": 3,
        },
    })
    partial_form["cells"][0] = report_partial_cell
    partial_form["counts"].update({
        "subjects_active_resolved": 0,
        "subjects_active_unresolved": 1,
        "ledger_blocking": 1,
        "referee_layout_mismatches": 1,
        "referee_layout_position_mismatches": 1,
        "comparisons": {
            **{name: 0 for name in COMPARISON_NAMES}, "stop": 1},
    })
    partial_form["status"] = "unevaluable"
    partial_form["reason"] = "1 lattice-ledger blockers"
    partial_report["status_reasons"] = [
        "corpus coverage or one or more forms are unevaluable",
        *partial_report["status_reasons"],
    ]
    partial_report["totals"].update({
        "subjects_active_resolved": 0,
        "subjects_active_unresolved": 1,
        "ledger_blocking": 1,
        "referee_layout_mismatches": 1,
        "referee_layout_position_mismatches": 1,
        "forms_ok": 0,
        "forms_unevaluable": 1,
        "comparisons": {
            **{name: 0 for name in COMPARISON_NAMES}, "stop": 1},
    })
    _resign_for_self_test(partial_report)
    partial_report_errors, partial_report_stats = validate_comb_referee_report(
        partial_report, child_exit=2, expected_forms=1, expected_subjects=1)
    if (partial_report_errors
            or partial_report_stats["referee_layout_mismatches"] != 1
            or partial_report_stats["referee_layout_position_mismatches"]
            != 1):
        failures.append(
            "a partial-anchor cell must recompute the report mismatch "
            "totals: " + "; ".join(partial_report_errors[:3]))
    forged_partial_totals = clone(partial_report)
    forged_partial_totals["totals"]["referee_layout_mismatches"] = 0
    _resign_for_self_test(forged_partial_totals)
    if not any(
            "referee_layout_mismatches" in error
            for error in validate_comb_referee_report(
                forged_partial_totals, child_exit=2,
                expected_forms=1, expected_subjects=1)[0]):
        failures.append(
            "a forged partial-anchor mismatch total must be UNEVALUABLE")
    envelope_errors = validate_comb_referee_envelope(
        envelope, raw_payload, report, snapshot)
    if envelope_errors:
        failures.append(
            "OS=false with explicit host TCB and enforceable application scope "
            "must validate: " + "; ".join(envelope_errors[:3]))
    # Persisted envelopes use sort_keys=True, so a JSON round trip must not
    # turn the lexical object-key order into a different owner order.  The
    # canonical order is the recorded layout cell-stream position, so both
    # cases below have to survive: one where the stream agrees with the cell
    # numerals and one where it does not.  A cell id is a continuity
    # identifier -- lattice.py hands a cell created by a partition repair a
    # fresh high number while it sits mid-page -- so a key that re-derives the
    # order from p<page>c<n> is right only by luck, and the second case is the
    # one that catches it.
    def _round_tripped_owner_ids(
            stream_positions: dict[str, int]) -> list[str] | None:
        registry = clone(snapshot["layout_bindings"]["fixture-1"])
        cells = {}
        for cell_id, stream_index in stream_positions.items():
            projected = clone(registry["cells"]["p1c1"])
            projected.update({
                "cell": cell_id, "legacy_cell_id": cell_id,
                "cell_id": cell_id, "stream_index": stream_index,
            })
            cells[cell_id] = projected
        registry["cells"] = cells
        registry["audit_expected_ids"] = sorted(
            stream_positions, key=lambda cell_id: stream_positions[cell_id])
        registry = json.loads(json.dumps(registry, sort_keys=True))
        try:
            return _layout_audit_owner_ids(registry)
        except Exception as error:  # noqa: BLE001 - self-test names breakage
            failures.append(
                "sorted-key layout owner registry must remain evaluable: "
                f"{type(error).__name__}: {error}")
            return None

    lexical_case = _round_tripped_owner_ids({"p1c2": 0, "p1c10": 1})
    if lexical_case is not None and lexical_case != ["p1c2", "p1c10"]:
        failures.append(
            "sorted-key layout owner registry changed canonical order")
    numeral_case = _round_tripped_owner_ids({"p1c10": 0, "p1c2": 1})
    if numeral_case is not None and numeral_case != ["p1c10", "p1c2"]:
        failures.append(
            "layout owner registry re-derived its order from the cell "
            "numerals instead of the recorded cell-stream position")
    missing_stream_position = clone(snapshot["layout_bindings"]["fixture-1"])
    missing_stream_position["cells"]["p1c1"].pop("stream_index")
    try:
        _layout_audit_owner_ids(missing_stream_position)
    except CombRefereeScopeError:
        pass
    else:
        failures.append(
            "a layout owner registry without a recorded stream position must "
            "fail closed rather than fall back to the cell numerals")
    sorted_snapshot = json.loads(json.dumps(snapshot, sort_keys=True))
    sorted_envelope = json.loads(json.dumps(envelope, sort_keys=True))
    sorted_envelope_errors = validate_comb_referee_envelope(
        sorted_envelope, raw_payload, report, sorted_snapshot)
    if sorted_envelope_errors:
        failures.append(
            "a persisted sorted-key comb-referee envelope must validate: "
            + "; ".join(sorted_envelope_errors[:3]))
    if _comb_referee_outcome(
            report, report_stats, expected_forms=1,
            expected_subjects=1).verdict is not Verdict.PASS:
        failures.append(
            "outer application attestation must elevate a producer-shaped "
            "raw standalone-attestation UNEVALUABLE report")
    if snapshot_pair_errors(snapshot, snapshot):
        failures.append("an identical clean application snapshot must validate")

    dirty = clone(snapshot)
    dirty["git"]["worktree_clean"] = False
    if not snapshot_pair_errors(snapshot, dirty):
        failures.append("a dirty before/after snapshot must fail closed")
    wrong_revision = clone(snapshot)
    wrong_revision["git"]["commit"] = "3" * 40
    if not snapshot_pair_errors(snapshot, wrong_revision):
        failures.append("a wrong HEAD revision must fail closed")
    mutated_snapshot = clone(snapshot)
    mutated_snapshot["audit"]["sha256"] = "4" * 64
    if not snapshot_pair_errors(snapshot, mutated_snapshot):
        failures.append("an input mutation during the referee run must fail closed")

    if not validate_comb_referee_report(
            None, expected_forms=1, expected_subjects=1)[0]:
        failures.append("a missing comb-referee report must be UNEVALUABLE")
    digest_bad = clone(report)
    digest_bad["status_reasons"] = ["mutated after signing"]
    if not validate_comb_referee_report(
            digest_bad, child_exit=2, expected_forms=1,
            expected_subjects=1)[0]:
        failures.append("a digest-bad comb-referee report must be UNEVALUABLE")
    partial = clone(report)
    partial["forms"] = []
    _resign_for_self_test(partial)
    if not validate_comb_referee_report(
            partial, child_exit=2, expected_forms=1,
            expected_subjects=1)[0]:
        failures.append("a partial comb-referee report must be UNEVALUABLE")
    stale_current = clone(snapshot)
    stale_current["runtime"]["python"]["sha256"] = "5" * 64
    if not validate_comb_referee_envelope(
            envelope, raw_payload, report, stale_current):
        failures.append("a stale comb-referee envelope must be UNEVALUABLE")
    non_enforceable = clone(envelope)
    non_enforceable["enforceable"] = False
    _resign_for_self_test(non_enforceable)
    if not validate_comb_referee_envelope(
            non_enforceable, raw_payload, report, snapshot):
        failures.append("a non-enforceable application scope must be UNEVALUABLE")
    no_host_tcb = clone(envelope)
    no_host_tcb["host_tcb_required"] = False
    _resign_for_self_test(no_host_tcb)
    if not validate_comb_referee_envelope(
            no_host_tcb, raw_payload, report, snapshot):
        failures.append(
            "OS=false is valid only with an explicit required host TCB")
    incomplete_scope = clone(envelope)
    incomplete_scope["application_scope_complete"] = False
    _resign_for_self_test(incomplete_scope)
    if not validate_comb_referee_envelope(
            incomplete_scope, raw_payload, report, snapshot):
        failures.append("an incomplete application scope must be UNEVALUABLE")
    wrong_executable = clone(envelope)
    wrong_executable["invocation"]["executable"] = "/tmp/shadow-python"
    _resign_for_self_test(wrong_executable)
    if not validate_comb_referee_envelope(
            wrong_executable, raw_payload, report, snapshot):
        failures.append("a substituted invocation executable must fail closed")
    public_output = clone(envelope)
    public_output["invocation"]["output"] = "build/comb-referee.json"
    _resign_for_self_test(public_output)
    if not validate_comb_referee_envelope(
            public_output, raw_payload, report, snapshot):
        failures.append("a non-private child output contract must fail closed")
    invented_invocation = clone(envelope)
    invented_invocation["invocation"]["invented"] = True
    _resign_for_self_test(invented_invocation)
    if not validate_comb_referee_envelope(
            invented_invocation, raw_payload, report, snapshot):
        failures.append("an inexact invocation schema must fail closed")

    def mutation_errors(mutator: Callable[[dict[str, Any]], None]) -> list[str]:
        mutated = clone(report)
        mutator(mutated)
        _resign_for_self_test(mutated)
        return validate_comb_referee_report(
            mutated, child_exit=2, expected_forms=1,
            expected_subjects=1)[0]

    def bound_application_verdict(
            candidate: dict[str, Any], scope: dict[str, Any],
            ) -> Verdict:
        errors, stats = validate_comb_referee_report(
            candidate, child_exit=2, expected_forms=1,
            expected_subjects=1)
        errors.extend(report_binding_errors(candidate, scope, stats))
        if errors:
            return Verdict.UNEVALUABLE
        return _comb_referee_outcome(
            candidate, stats, expected_forms=1, expected_subjects=1).verdict

    def application_verdict(
            mutator: Callable[[dict[str, Any]], None]) -> Verdict:
        mutated = clone(report)
        mutator(mutated)
        _resign_for_self_test(mutated)
        return bound_application_verdict(mutated, snapshot)

    if not mutation_errors(
            lambda value: value["forms"][0]["counts"].update({"invented": 0})):
        failures.append("an inexact per-form count schema must be UNEVALUABLE")
    if not mutation_errors(
            lambda value: value["forms"][0]["counts"].update(
                {"subjects_active_resolved": 0})):
        failures.append("a false subject-state total must be UNEVALUABLE")
    if not mutation_errors(
            lambda value: value["forms"][0]["cells"][0].update(
                {"ledger_blocks_gate": True})):
        failures.append("a false ledger blocking relation must be UNEVALUABLE")
    if not mutation_errors(
            lambda value: value["forms"][0]["cells"][0].update(
                {"transition_reason": "invented"})):
        failures.append("a false transition status/reason must be UNEVALUABLE")
    if not mutation_errors(
            lambda value: value["forms"][0]["cells"][0].update({"emitted": 0})):
        failures.append("a false emission-mismatch total must be UNEVALUABLE")
    if not mutation_errors(
            lambda value: value["forms"][0]["cells"][0]["referee"].update(
                {"compartments": 3})):
        failures.append("a false referee-layout total must be UNEVALUABLE")
    if not mutation_errors(
            lambda value: value["forms"][0]["cells"][0]["bbox"].__setitem__(
                0, float("nan"))):
        failures.append("non-finite cell geometry must be UNEVALUABLE")
    if not mutation_errors(
            lambda value: value["forms"][0].update({
                "status": "unevaluable", "reason": "fabricated"})):
        failures.append("a fabricated per-form status must be UNEVALUABLE")
    if not mutation_errors(
            lambda value: value["totals"].update(
                {"audit_evidence_complete_forms": 0})):
        failures.append("a false audit-completeness aggregate must be UNEVALUABLE")
    if not mutation_errors(
            lambda value: value["totals"].update(
                {"referee_attestation_complete": True})):
        failures.append("a false raw-attestation aggregate must be UNEVALUABLE")
    raw_reason_substitution = lambda value: value["attestation"].update({
        "incomplete_reasons": ["fatal source ambiguity"]})
    raw_reason_addition = lambda value: value["attestation"].update({
        "incomplete_reasons": [
            *RAW_REFEREE_INCOMPLETE_REASONS, "unrelated fatal blocker"]})
    raw_future_substitution = lambda value: value["attestation"].update({
        "future_gate_required": "arbitrary nonempty text"})
    for label, mutator in (
            ("substituted raw attestation reason", raw_reason_substitution),
            ("extra raw attestation reason", raw_reason_addition),
            ("substituted future-gate reason", raw_future_substitution)):
        if application_verdict(mutator) is Verdict.PASS:
            failures.append(f"{label} must prevent exit-2 elevation")

    def rewrite_outer_audit_inventory(
            candidate: dict[str, Any], scope: dict[str, Any],
            ids: list[str],
            ) -> None:
        scope["layout_bindings"]["fixture-1"]["audit_expected_ids"] = ids
        outer = scope["audit"]["forms"]["fixture-1"]["assertion_relation"]
        outer.update({
            "combs_expected": len(ids),
            "combs_checked": len(ids),
            "expected_comb_ids": ids,
            "checked_comb_ids": ids,
            "emitted_comb_ids": ids,
            "owner_certificates_valid": len(ids),
            "owner_certificates_invalid": 0,
        })
        evidence = candidate["forms"][0]["audit_evidence"]
        for key, value in outer.items():
            evidence[key] = clone(value)
        scope["audit"]["forms_sha256"] = canonical_digest(
            scope["audit"]["forms"])
        _resign_for_self_test(candidate)

    audit_only_report = clone(report)
    audit_only_scope = clone(snapshot)
    rewrite_outer_audit_inventory(
        audit_only_report, audit_only_scope, ["p1c1", "p1c2"])
    if bound_application_verdict(
            audit_only_report, audit_only_scope) is Verdict.PASS:
        failures.append(
            "an audit-only extra owner must prevent exit-2 elevation")

    report_only_report = clone(report)
    report_only_scope = clone(snapshot)
    rewrite_outer_audit_inventory(
        report_only_report, report_only_scope, [])
    if bound_application_verdict(
            report_only_report, report_only_scope) is Verdict.PASS:
        failures.append(
            "an audit-missing/report-only owner must prevent exit-2 elevation")

    opaque_mutations: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
        (
            "emission binding error",
            lambda value: value["forms"][0]["emission_binding_errors"].append(
                "fatal emission binding"),
        ),
        (
            "audit error",
            lambda value: value["forms"][0]["audit_evidence"]["errors"].append(
                "fatal audit"),
        ),
        (
            "manifest error",
            lambda value: value["forms"][0]["audit_evidence"][
                "manifest_binding"]["errors"].append("fatal manifest"),
        ),
        (
            "ledger error",
            lambda value: value["forms"][0]["audit_evidence"][
                "ledger_binding"]["errors"].append("fatal ledger"),
        ),
        (
            "lattice error",
            lambda value: value["forms"][0].update({
                "lattice_evidence": {
                    "complete": False, "errors": ["fatal lattice"]}}),
        ),
        (
            "Poppler error",
            lambda value: value["forms"][0]["poppler"].update({
                "error": "fatal Poppler"}),
        ),
        (
            "page error",
            lambda value: value["forms"][0]["pages"][0].update({
                "status": "error", "reason": "fatal page"}),
        ),
        (
            "measured-referee error",
            lambda value: value["forms"][0]["cells"][0]["referee"].update({
                "error": "fatal source"}),
        ),
    ]
    for label, mutator in opaque_mutations:
        if application_verdict(mutator) is Verdict.PASS:
            failures.append(f"opaque {label} must prevent exit-2 elevation")

    provenance_mutations: list[
        tuple[str, Callable[[dict[str, Any]], None]]
    ] = [
        (
            "extra dependency role",
            lambda value: value["provenance"]["dependencies"].update({
                "evil": {"file": "/tmp/evil.py"}}),
        ),
        (
            "extra audit child dependency",
            lambda value: value["provenance"]["dependencies"]["audit"][
                "dependencies"].append({
                    "file": "/tmp/evil.py", "bytes": 1,
                    "sha256": "e" * 64, "expected_sha256": "e" * 64,
                }),
        ),
        (
            "duplicate audit child dependency",
            lambda value: value["provenance"]["dependencies"]["audit"][
                "dependencies"].append(clone(
                    value["provenance"]["dependencies"]["audit"][
                        "dependencies"][0])),
        ),
        (
            "extra report input",
            lambda value: value["inputs"].update({"evil": True}),
        ),
        (
            "extra provenance field",
            lambda value: value["provenance"].update({"evil": True}),
        ),
        (
            "extra runtime field",
            lambda value: value["provenance"]["runtime"].update({
                "evil": True}),
        ),
    ]
    for label, mutator in provenance_mutations:
        if not mutation_errors(mutator):
            failures.append(f"{label} must fail the exact provenance closure")

    # Z1 renamed this message when the partition became three-way; the
    # mutation below still proves the same check refuses the same forgery.
    false_report_source_partition = mutation_errors(
        lambda value: value["forms"][0]["audit_evidence"].update({
            "source_u_frame_evaluable": 1,
            "source_certified_unframed_evaluable": 1,
        }))
    if not any("source frame/unframed/reviewed partition" in error
               for error in false_report_source_partition):
        failures.append(
            "a false published source frame/unframed partition must fail")

    # Z1: the reviewed-topology term is a third evaluability class, so it is
    # a third way to forge the partition. Each forgery is refused separately.
    reviewed_partition_mutations: list[
        tuple[str, str, Callable[[dict[str, Any]], None]]
    ] = [
        (
            "a reviewed subject with no matching evaluability must fail",
            "source frame/unframed/reviewed partition",
            lambda value: value["forms"][0]["audit_evidence"].update({
                "decided_by_review": 1,
                "decided_by_review_subjects": [{
                    "cell": value["forms"][0]["audit_evidence"][
                        "expected_comb_ids"][0],
                    "printed": 1, "latticed": 1,
                    "reviewed_comb_topology": {
                        "criterion": "reviewed-comb-topology-v1",
                        "valid": True, "compartments": 1,
                        "source_sha256": "0" * 64,
                        "reviewer": "self-test", "citation": "self-test",
                    },
                }],
            }),
        ),
        (
            "a reviewed count without subjects must fail",
            "source accounting is malformed",
            lambda value: value["forms"][0]["audit_evidence"].update({
                "decided_by_review": 1,
                "decided_by_review_subjects": [],
            }),
        ),
        (
            "a reviewed subject outside the checked cells must fail",
            "source accounting is malformed",
            lambda value: value["forms"][0]["audit_evidence"].update({
                "decided_by_review": 1,
                "decided_by_review_subjects": [{
                    "cell": "p9c999", "printed": 1, "latticed": 1,
                    "reviewed_comb_topology": {
                        "criterion": "reviewed-comb-topology-v1",
                        "valid": True, "compartments": 1,
                        "source_sha256": "0" * 64,
                        "reviewer": "self-test", "citation": "self-test",
                    },
                }],
            }),
        ),
        (
            "duplicate reviewed subjects must fail",
            "source accounting is malformed",
            lambda value: value["forms"][0]["audit_evidence"].update({
                "decided_by_review": 2,
                "decided_by_review_subjects": [{
                    "cell": value["forms"][0]["audit_evidence"][
                        "expected_comb_ids"][0],
                    "printed": 1, "latticed": 1,
                    "reviewed_comb_topology": {
                        "criterion": "reviewed-comb-topology-v1",
                        "valid": True, "compartments": 1,
                        "source_sha256": "0" * 64,
                        "reviewer": "self-test", "citation": "self-test",
                    },
                }] * 2,
            }),
        ),
    ]
    for label, needle, mutator in reviewed_partition_mutations:
        if not any(needle in error for error in mutation_errors(mutator)):
            failures.append(label)

    measured_certificate_mutations: list[
        tuple[str, Callable[[dict[str, Any]], None]]
    ] = [
        (
            "false source-position boolean",
            lambda value: value["forms"][0]["cells"][0]["referee"].update({
                "positions_match": False}),
        ),
        # R1 (F232): rail-derivation forgeries that keep the compartment
        # arithmetic VALID, so nothing but _rail_derivation_errors can refuse
        # them -- a mutation another check would also catch proves nothing
        # about this one.  Rails stay at the owner's edges throughout.
        (
            "prose-refutation basis on an edge rail",
            lambda value: value["forms"][0]["cells"][0]["referee"].update({
                "rail_derivation": {
                    "left": {"basis": "prose-refuted-outer-region",
                             "from_x": 0.0, "span_pt": 0.0, "glyphs": 3},
                    "right": {"basis": "owner-edge"},
                }}),
        ),
        (
            "wall basis whose wall is no measured boundary",
            lambda value: value["forms"][0]["cells"][0]["referee"].update({
                "rail_derivation": {
                    "left": {"basis": "wall-outside-run", "wall_x": 0.0},
                    "right": {"basis": "owner-edge"},
                }}),
        ),
        (
            "rail derivation missing a side",
            lambda value: value["forms"][0]["cells"][0]["referee"].update({
                "rail_derivation": {
                    "left": {"basis": "owner-edge"},
                }}),
        ),
        (
            "rail derivation with an unsupported basis",
            lambda value: value["forms"][0]["cells"][0]["referee"].update({
                "rail_derivation": {
                    "left": {"basis": "prose-and-structure-conflict",
                             "glyphs": 3, "structure_components": 1},
                    "right": {"basis": "owner-edge"},
                }}),
        ),
        (
            "source coordinates detached from the lattice",
            lambda value: value["forms"][0]["cells"][0]["referee"].update({
                "source_divider_x": [4.0],
                "chosen_topology": [4.0],
                "anchor_matches": [{
                    "layout_x": 5.0, "source_x": 4.0, "delta_pt": -1.0,
                }],
                "components": [{
                    "x": 4.0, "x0": 3.9, "x1": 4.1, "tone": 0.0,
                    "elements": ["forged-divider"], "clipped": False,
                }],
                "topology_coverage_pt": {"4.0": 10.0},
                "positions_match": True,
            }),
        ),
        (
            "unproven subject gap",
            lambda value: value["forms"][0]["cells"][0]["referee"][
                "unproven_subject_gaps"].append({"reason": "forged"}),
        ),
        # A comb is counted between the rails the referee measured. Counting
        # it across the whole rectangle again is the defect this key exists to
        # close, and each way of forging a rail is refused separately.
        (
            "compartments counted across the rectangle, not the rails",
            lambda value: value["forms"][0]["cells"][0]["referee"].update({
                "source_rail_x": [5.0, 10.0]}),
        ),
        (
            "a rail the referee never measured",
            lambda value: value["forms"][0]["cells"][0]["referee"].update({
                "source_rail_x": [4.0, 10.0]}),
        ),
        (
            "a rail outside the owner",
            lambda value: value["forms"][0]["cells"][0]["referee"].update({
                "source_rail_x": [-1.0, 10.0]}),
        ),
        (
            "rails that do not enclose anything",
            lambda value: value["forms"][0]["cells"][0]["referee"].update({
                "source_rail_x": [10.0, 0.0]}),
        ),
        (
            "zero measured span",
            lambda value: value["forms"][0]["cells"][0]["referee"].update({
                "measured_span_pt": 0.0}),
        ),
        (
            "negative contract span",
            lambda value: value["forms"][0]["cells"][0]["referee"].update({
                "contract_span_pt": -1.0}),
        ),
        (
            "non-finite measured reason",
            lambda value: value["forms"][0]["cells"][0]["referee"].update({
                "reason": float("nan")}),
        ),
        (
            "zero vector-paint source page",
            lambda value: value["forms"][0]["pages"][0].update({
                "vector_paints": 0}),
        ),
    ]
    # Direct probes of _rail_derivation_errors for the relations the shared
    # report fixture cannot express without tripping OTHER checks first: an
    # interior rail must name its measurement, a one-glyph refutation refutes
    # nothing, and span arithmetic is re-derived.
    probe_cell = {"cell": "p1c9", "bbox": [0.0, 0.0, 10.0, 10.0]}
    def probe_referee(rails, derivation):
        return {
            "source_rail_x": rails,
            "source_divider_x": [5.0],
            "rail_derivation": derivation,
        }
    assert not _rail_derivation_errors(
        "probe", probe_referee([0.0, 10.0], {
            "left": {"basis": "owner-edge"},
            "right": {"basis": "owner-edge"}}), probe_cell)
    assert not _rail_derivation_errors(
        "probe", probe_referee([5.0, 10.0], {
            "left": {"basis": "prose-refuted-outer-region",
                     "from_x": 0.0, "span_pt": 5.0, "glyphs": 3},
            "right": {"basis": "owner-edge"}}), probe_cell)
    assert not _rail_derivation_errors(
        "probe", probe_referee([5.0, 10.0], {
            "left": {"basis": "wall-outside-run", "wall_x": 5.0},
            "right": {"basis": "owner-edge"}}), probe_cell)
    for broken_rails, broken_derivation in (
            ([5.0, 10.0], {"left": {"basis": "owner-edge"},
                           "right": {"basis": "owner-edge"}}),
            ([5.0, 10.0], {"left": {"basis": "prose-refuted-outer-region",
                                    "from_x": 0.0, "span_pt": 5.0,
                                    "glyphs": 1},
                           "right": {"basis": "owner-edge"}}),
            ([5.0, 10.0], {"left": {"basis": "prose-refuted-outer-region",
                                    "from_x": 0.0, "span_pt": 4.0,
                                    "glyphs": 3},
                           "right": {"basis": "owner-edge"}}),
    ):
        assert _rail_derivation_errors(
            "probe", probe_referee(broken_rails, broken_derivation),
            probe_cell), (broken_rails, broken_derivation)

    for label, mutator in measured_certificate_mutations:
        if not mutation_errors(mutator):
            failures.append(f"{label} must invalidate measured source evidence")

    # ---- C3-A: the gate's own composite mirror ---------------------------
    #
    # These are the gate's independent re-derivations, never the referee's
    # published labels: a reviewed composite is scored on its corroboration,
    # and every way that certificate can be wrong is proven to be caught.
    def composite_gate_cell(**overrides):
        value = {
            "ledger_state": "active_composite",
            "emitted": None,
            "latticed": 4,
            "audit_printed": None,
            "referee": {
                "status": "composite",
                "criterion": "source-partition-edge-in-final-picture-v1",
                "corroborated": True,
                "reason": "the source corroborates the reviewed composite's "
                          "suppression claim",
            },
        }
        value.update(overrides)
        return value

    assert _comparison_for_cell(composite_gate_cell(), True)[0] == "agree"
    assert _comparison_for_cell(composite_gate_cell(), False)[0] == "agree"
    assert _comparison_for_cell(composite_gate_cell(referee={
        "status": "composite",
        "criterion": "source-partition-edge-in-final-picture-v1",
        "corroborated": False,
        "reason": "refuted"}), True)[0] == "stop"
    assert _comparison_for_cell(
        composite_gate_cell(emitted=4), True)[0] == "stop"
    assert _comparison_for_cell(composite_gate_cell(referee={
        "status": "unevaluable", "reason": "x"}), True)[0] == "unevaluable"
    assert _transition_for_cell("active_composite", "agree") == (
        "none", "reviewed composite transition is already applied")
    # C4a mirror: a signed resolution re-derived against this run.
    def gate_resolved_cell(**overrides):
        value = {
            "ledger_state": "active_resolved",
            "latticed": 4, "emitted": 4, "audit_printed": 4,
            "emitted_indexes_valid": True,
            "referee": {"status": "measured", "compartments": 4,
                        "positions_match": True},
            "resolution_certificate": {
                "criterion": "reviewed-ledger-resolution-v1",
                "four_way": {"lattice": 4, "audit": 4,
                             "emitted": 4, "referee": 4},
            },
        }
        value.update(overrides)
        return value

    assert _comparison_for_cell(gate_resolved_cell(), True)[0] == "agree"
    for overrides in ({"audit_printed": 5}, {"latticed": 5, "emitted": 5},
                      {"referee": {"status": "measured", "compartments": 5,
                                   "positions_match": True}}):
        drifted = _comparison_for_cell(gate_resolved_cell(**overrides), True)
        assert drifted[0] == "stop", (overrides, drifted)
        assert "moved since this resolution was reviewed" in drifted[1]
    assert _comparison_for_cell(gate_resolved_cell(resolution_certificate={
        "criterion": "reviewed-ledger-resolution-v1"}), True)[0] == "stop"
    # C4b coverage guard, proven able to fail in all three directions.
    def cov(cells): return _reviewed_registry_coverage_errors(cells)
    _reg = _load_review_registry()
    _k = ("fixture-1999", 1, "p1c7")
    _reg.REVIEWED_LEDGER_RESOLUTIONS[_k] = {
        "subject_key": "p1@0,0,1,1", "source_sha256": "ab" * 32,
        "four_way": {"lattice": 2, "audit": 2, "emitted": 2, "referee": 2},
        "reviewer": "self-test", "date": "2026-08-15", "citation": "self-test"}
    try:
        _cert = {"registry_key": [_k[0], _k[1], _k[2]]}
        _one = [{"cell": _k[2], "resolution_certificate": _cert}]
        assert cov(_one) == [], cov(_one)
        assert any("applied 2 times" in e for e in cov(_one + _one))
        assert any("applied nowhere" in e for e in cov([]))
        forged = [{"cell": "p9c9", "resolution_certificate":
                   {"registry_key": ["no-such-form", 1, "p9c9"]}}]
        errs = cov(forged)
        assert any("names no reviewed entry" in e for e in errs), errs
        assert any("applied nowhere" in e for e in errs), errs
    finally:
        _reg.REVIEWED_LEDGER_RESOLUTIONS.pop(_k, None)
    assert "active_composite" in LEDGER_STATES
    # The composite certificate schema the cell walk enforces.
    assert COMPOSITE_REFEREE_KEYS == {
        "status", "criterion", "corroborated", "reason"}
    assert all(
        criterion.endswith("-v1")
        for criterion in COMPOSITE_SUPPRESSION_CRITERIA)

    def layout_disagreement(value: dict[str, Any], *, global_total: int) -> None:
        cell = value["forms"][0]["cells"][0]
        cell["referee"]["compartments"] = 3
        cell["referee"]["source_divider_x"] = [5.0, 7.0]
        cell["referee"]["extra_divider_x"] = [7.0]
        cell["referee"]["chosen_topology"] = [5.0, 7.0]
        cell["referee"]["topology_coverage_pt"] = {"5.0,7.0": 10.0}
        cell["referee"]["components"].append({
            "x": 7.0,
            "x0": 6.9,
            "x1": 7.1,
            "tone": 0.0,
            "elements": ["fixture-extra-divider"],
            "clipped": False,
        })
        cell["four_way"]["referee"] = 3
        cell["comparison_status"] = "stop"
        cell["comparison_reason"] = (
            "lattice and audit agree against the independent referee")
        form = value["forms"][0]
        form["counts"]["referee_layout_mismatches"] = 1
        form["counts"]["comparisons"]["agree"] = 0
        form["counts"]["comparisons"]["stop"] = 1
        form["status"] = "disagreement"
        form["reason"] = "one or more four-way comparisons disagree"
        value["totals"]["referee_layout_mismatches"] = global_total
        value["totals"]["comparisons"]["agree"] = 0
        value["totals"]["comparisons"]["stop"] = 1
        value["totals"]["forms_ok"] = 0
        value["totals"]["forms_disagreement"] = 1
        value["status_reasons"] = [
            "one or more four-way form comparisons disagree",
            "standalone referee runtime/application attestation is incomplete "
            "and non-enforceable",
        ]

    missing_global = clone(report)
    layout_disagreement(missing_global, global_total=0)
    _resign_for_self_test(missing_global)
    missing_global_errors, _missing_stats = validate_comb_referee_report(
        missing_global, child_exit=2, expected_forms=1, expected_subjects=1)
    if not any("referee_layout_mismatches" in error
               for error in missing_global_errors):
        failures.append(
            "cell/form referee mismatch must derive into the global total")

    complete_disagreement = clone(report)
    layout_disagreement(complete_disagreement, global_total=1)
    _resign_for_self_test(complete_disagreement)
    complete_errors, complete_stats = validate_comb_referee_report(
        complete_disagreement, child_exit=2,
        expected_forms=1, expected_subjects=1)
    if (complete_errors or _comb_referee_outcome(
            complete_disagreement, complete_stats,
            expected_forms=1, expected_subjects=1).verdict is not Verdict.FAIL):
        failures.append(
            "a fully derived independent-referee disagreement must be FAIL")

    position_report = clone(report)
    position_cell = position_report["forms"][0]["cells"][0]
    position_cell["referee"]["positions_match"] = False
    position_cell["comparison_status"] = "stop"
    position_cell["comparison_reason"] = (
        "referee positions disagree with lattice anchors")
    position_form = position_report["forms"][0]
    position_form["counts"]["referee_layout_position_mismatches"] = 1
    position_form["counts"]["comparisons"]["agree"] = 0
    position_form["counts"]["comparisons"]["stop"] = 1
    position_form["status"] = "disagreement"
    position_form["reason"] = "one or more four-way comparisons disagree"
    position_report["totals"]["comparisons"]["agree"] = 0
    position_report["totals"]["comparisons"]["stop"] = 1
    position_report["totals"]["forms_ok"] = 0
    position_report["totals"]["forms_disagreement"] = 1
    position_report["status_reasons"] = [
        "one or more four-way form comparisons disagree",
        "standalone referee runtime/application attestation is incomplete "
        "and non-enforceable",
    ]
    _resign_for_self_test(position_report)
    position_errors, _position_stats = validate_comb_referee_report(
        position_report, child_exit=2,
        expected_forms=1, expected_subjects=1)
    if not any("referee_layout_position_mismatches" in error
               for error in position_errors):
        failures.append(
            "cell/form position mismatch must derive into the global total")

    def emission_disagreement(value: dict[str, Any]) -> None:
        cell = value["forms"][0]["cells"][0]
        cell["emitted"] = 0
        cell["four_way"]["emitted"] = 0
        cell["comparison_status"] = "stale-generation"
        cell["comparison_reason"] = (
            "emitted physical slots disagree with lattice")
        form = value["forms"][0]
        form["counts"]["emission_layout_mismatches"] = 1
        form["counts"]["comparisons"]["agree"] = 0
        form["counts"]["comparisons"]["stale-generation"] = 1
        form["counts"]["comparisons"]["unevaluable"] = 0
        form["counts"]["unevaluable"] = 0
        form["status"] = "disagreement"
        form["reason"] = "one or more four-way comparisons disagree"
        value["totals"]["comparisons"]["agree"] = 0
        value["totals"]["comparisons"]["stale-generation"] = 1
        value["totals"]["comparisons"]["unevaluable"] = 0
        value["totals"]["combs_unevaluable"] = 0
        value["totals"]["forms_ok"] = 0
        value["totals"]["forms_disagreement"] = 1
        value["totals"]["forms_unevaluable"] = 0
        value["status_reasons"] = [
            "one or more four-way form comparisons disagree",
            "standalone referee runtime/application attestation is incomplete "
            "and non-enforceable",
        ]

    emission_report = clone(report)
    emission_disagreement(emission_report)
    _resign_for_self_test(emission_report)
    emission_errors, emission_stats = validate_comb_referee_report(
        emission_report, child_exit=2, expected_forms=1, expected_subjects=1)
    if (emission_errors
            or emission_stats["emission_layout_mismatches"] != 1
            or _comb_referee_outcome(
                emission_report, emission_stats,
                expected_forms=1,
                expected_subjects=1).verdict is not Verdict.FAIL):
        failures.append(
            "emission mismatch must derive globally and prevent PASS: "
            + "; ".join(emission_errors[:3]))

    def add_inference(value: dict[str, Any]) -> None:
        value["forms"][0]["inferences"].append({
            "page": 1,
            "subject_key": "p1@20,0,30,10",
            "cell_id": "p1c2",
            "state": INFERENCE_STATE,
            "blocks_gate": True,
            "reason_codes": ["unreviewed"],
            "bbox": [20.0, 0.0, 30.0, 10.0],
            "topology_sha256": sha256_bytes(b"inference"),
            "ledger_evidence": {},
            "emitted_evidence": None,
        })

    if not mutation_errors(add_inference):
        failures.append("a false inference/blocker total must be UNEVALUABLE")
    duplicate_cell_errors = mutation_errors(
        lambda value: value["forms"][0]["cells"].append(
            clone(value["forms"][0]["cells"][0])))
    if not any("duplicate cell" in error or "duplicate subject" in error
               for error in duplicate_cell_errors):
        failures.append("duplicate cell/subject identities must be UNEVALUABLE")
    duplicate_slug_errors = mutation_errors(
        lambda value: value["forms"].append(clone(value["forms"][0])))
    if not any("duplicate slug" in error for error in duplicate_slug_errors):
        failures.append("duplicate form slugs must be UNEVALUABLE")

    bound_form = report["forms"][0]
    coherent_identity = clone(bound_form)
    coherent_identity["cells"][0].update({
        "cell": "p9c9", "legacy_cell_id": "p9c9", "cell_id": "p9c9",
        "subject_key": "p9@100,0,110,10",
    })
    if not form_binding_errors(coherent_identity, snapshot):
        failures.append("coherent fabricated cell identity must be unbound")
    coherent_topology = clone(bound_form)
    coherent_topology["cells"][0].update({
        "subject_key": "p1@100,0,110,10",
        "bbox": [100.0, 0.0, 110.0, 10.0],
        "latticed": 3,
        "lattice_divider_x": [103.0, 107.0],
        "emitted": 3,
    })
    if not form_binding_errors(coherent_topology, snapshot):
        failures.append("coherent fabricated cell topology must be unbound")
    invented_ledger = clone(bound_form)
    invented_ledger["cells"][0].update({
        "ledger_topology_sha256": "f" * 64,
        "ledger_evidence": {"invented": ["anything"]},
        "emitted_evidence": {"invented": True},
    })
    if not form_binding_errors(invented_ledger, snapshot):
        failures.append("invented ledger/emission evidence must be unbound")
    stale_artifact = clone(bound_form)
    stale_artifact["artifacts"]["ir_sha256"] = "6" * 64
    if not form_binding_errors(stale_artifact, snapshot):
        failures.append("a stale per-form IR hash must be UNEVALUABLE")
    stale_optional_guide = clone(bound_form)
    stale_optional_guide["artifacts"]["guide_html_sha256"] = "7" * 64
    if not form_binding_errors(stale_optional_guide, snapshot):
        failures.append("a stale optional guide HTML hash must be UNEVALUABLE")
    stale_provenance = clone(bound_form)
    stale_provenance["artifacts"]["tracked_provenance_sha256"] = "8" * 64
    if not form_binding_errors(stale_provenance, snapshot):
        failures.append("a stale tracked provenance hash must be UNEVALUABLE")
    stale_source = clone(bound_form)
    stale_source["source"]["sha256"] = "9" * 64
    if not form_binding_errors(stale_source, snapshot):
        failures.append("a stale source PDF pin must be UNEVALUABLE")
    stale_audit_relation = clone(bound_form)
    stale_audit_relation["audit_evidence"]["holds"] = False
    if not form_binding_errors(stale_audit_relation, snapshot):
        failures.append("a false per-form audit relation must be UNEVALUABLE")
    mutated_outer_audit = clone(snapshot)
    mutated_outer_audit["audit"]["forms"]["fixture-1"]["inputs"]["ir"][
        "sha256"] = "a" * 64
    if not form_binding_errors(bound_form, mutated_outer_audit):
        failures.append("a mutated outer audit input must be UNEVALUABLE")
    # A cell may publish only the topology the outer offender ledger supports:
    # for a non-offender that is its own lattice count, so any other number is
    # fabricated no matter how confidently the relation is labelled.
    fabricated_cell_audit = clone(bound_form)
    fabricated_cell_audit["cells"][0]["audit_printed"] = 5
    fabricated_cell_audit["cells"][0]["audit_relation"] = (
        "complete-non-offender")
    fabricated_cell_audit["cells"][0]["four_way"]["audit"] = 5
    if not form_binding_errors(fabricated_cell_audit, snapshot):
        failures.append(
            "cell audit topology must bind to the outer offender ledger")
    withheld_cell_audit = clone(bound_form)
    withheld_cell_audit["cells"][0]["audit_printed"] = None
    withheld_cell_audit["cells"][0]["audit_relation"] = "unknown-truncated"
    withheld_cell_audit["cells"][0]["four_way"]["audit"] = None
    if not form_binding_errors(withheld_cell_audit, snapshot):
        failures.append(
            "a withheld cell audit topology must be UNEVALUABLE")
    orphan_snapshot = clone(snapshot)
    orphan_relation = orphan_snapshot["audit"]["forms"]["fixture-1"][
        "assertion_relation"]
    orphan_relation.update({
        "holds": False,
        "offender_count": 1,
        "offenders_published": 1,
        "offender_dimensions": {
            "orphan-cell": {
                "cell": "orphan-cell", "page": 1, "slots": 2,
                "latticed": None, "printed": None,
                "emitted_occurrences": 1,
                "layout_relation": "not-owned",
                "emission_state": "physical-slots",
                "failure_kinds": ["unexpected-emitted-comb"],
                "source_owner_certificate": None,
                "dimensions": {
                    "layout_mismatch": False,
                    "source_unevaluable": False,
                    "emission_invalid": False,
                    "emission_behind": True,
                    "position_mismatch": False,
                    "inventory_binding": True,
                },
            },
        },
    })
    orphan_form = clone(bound_form)
    for key, value in orphan_relation.items():
        orphan_form["audit_evidence"][key] = clone(value)
    if not any("orphaned" in error for error in form_binding_errors(
            orphan_form, orphan_snapshot)):
        failures.append("orphan outer audit offender must fail closed")
    duplicate_source = clone(snapshot)
    duplicate_relation = duplicate_source["source_pdfs"]["relations"][0]
    duplicate_candidate = clone(duplicate_relation["candidates"][0])
    duplicate_candidate["path"] = "duplicate/fixture.pdf"
    duplicate_relation["candidates"].append(duplicate_candidate)
    duplicate_relation["candidate_count"] = 2
    duplicate_relation["matching_count"] = 2
    duplicate_source["source_pdfs"]["candidate_file_count"] = 2
    duplicate_source["source_pdfs"]["sha256"] = canonical_digest(
        duplicate_source["source_pdfs"]["relations"])
    if not form_binding_errors(bound_form, duplicate_source):
        failures.append(
            "two byte-identical authoritative source PDFs must fail closed")

    raw_offender = {
        "cell": "p1c2", "page": 1, "slots": 1, "latticed": None,
        "printed": None, "printed_divider_x": [],
        "physical_slots": 1, "declared_slots": 1,
        "emitted_occurrences": 1,
        "layout_relation": "not-owned", "emission_relation": "unexpected",
        "emission_state": "physical-slots",
        "failure_kinds": ["unexpected-emitted-comb"],
        "why": "synthetic unexpected emission",
    }
    outer_assertion_relation = snapshot[
        "audit"]["forms"]["fixture-1"]["assertion_relation"]
    registry_offender = {
        "cell": "<comb-owner-registry>",
        "page": None,
        "slots": None,
        "latticed": None,
        "printed": None,
        "printed_divider_x": [],
        "physical_slots": None,
        "declared_slots": None,
        "emitted_occurrences": 0,
        "emission_state": "not-evaluated",
        "effective_emission_state": "not-evaluated",
        "source_owner_certificate": {
            "criterion": "exact-reviewed-layout-comb-subject-owner-v1",
            "valid": False,
            "reason": "synthetic global registry failure",
            "supplies_topology": False,
        },
        "layout_relation": "registry-invalid",
        "emission_relation": "not-evaluated",
        "failure_kinds": ["comb-owner-registry-invalid"],
        "why": "synthetic global registry failure",
    }
    registry_assertion = {
        **{
            key: outer_assertion_relation[key]
            for key in AUDIT_ASSERTION_SUMMARY_KEYS
        },
        "combs_expected": 0,
        "combs_checked": 0,
        "expected_comb_ids": [],
        "checked_comb_ids": [],
        "emitted_comb_ids": [],
        "owner_certificates_valid": 0,
        "owner_certificates_invalid": 0,
        "source_u_frame_evaluable": 0,
        "source_certified_unframed_evaluable": 0,
        "inventory_complete": False,
        "holds": False,
        "reason": "global owner registry is invalid",
        "offender_count": 1,
        "offenders_published": 1,
        "offenders_omitted": 0,
        "offenders_complete": True,
        "offenders": [registry_offender],
    }
    try:
        registry_relation = _normalise_outer_comb_assertion(
            registry_assertion)
    except CombRefereeScopeError as error:
        failures.append(
            f"complete red owner-registry evidence must validate: {error}")
    else:
        registry_dimensions = registry_relation.get(
            "offender_dimensions", {}).get("<comb-owner-registry>", {})
        if (registry_relation.get("holds") is not False
                or registry_relation.get("inventory_complete") is not False
                or registry_dimensions.get("dimensions", {}).get(
                    "inventory_binding") is not True):
            failures.append(
                "owner-registry pseudo offender must remain fail-closed")

    raw_assertion = {
        **{
            key: outer_assertion_relation[key]
            for key in AUDIT_ASSERTION_SUMMARY_KEYS
        },
        "holds": False,
        "reason": "one mismatch",
        "offender_count": 1,
        "offenders_published": 1,
        "offenders_omitted": 0,
        "offenders_complete": True,
        "offenders": [raw_offender],
    }
    raw_assertion.update({
        "emitted_comb_ids": ["p1c1", "p1c2"],
        "unexpected_emitted_comb_ids": ["p1c2"],
        "emitted_cell_binding_issues": 1,
        "inventory_complete": False,
        "emission_behind_layout": 1,
    })
    try:
        normalised_offenders = _normalise_outer_comb_assertion(raw_assertion)
    except CombRefereeScopeError as error:
        failures.append(f"complete outer offender ledger must validate: {error}")
        normalised_offenders = {}
    if set(normalised_offenders.get("offender_dimensions", {})) != {"p1c2"}:
        failures.append("outer offender ledger must publish an exact cell map")
    duplicated_offenders = clone(raw_assertion)
    duplicated_offenders["offenders"].append(clone(raw_offender))
    duplicated_offenders["offender_count"] = 2
    duplicated_offenders["offenders_published"] = 2
    try:
        _normalise_outer_comb_assertion(duplicated_offenders)
    except CombRefereeScopeError:
        pass
    else:
        failures.append("duplicate outer offender IDs must fail closed")
    truncated_offenders = clone(raw_assertion)
    truncated_offenders["offender_count"] = 2
    truncated_offenders["offenders_omitted"] = 1
    truncated_offenders["offenders_complete"] = False
    try:
        _normalise_outer_comb_assertion(truncated_offenders)
    except CombRefereeScopeError:
        pass
    else:
        failures.append("truncated outer offender publication must fail closed")
    false_offender_summary = clone(raw_assertion)
    false_offender_summary.update({
        "inventory_complete": True,
        "emitted_cell_binding_issues": 0,
        "emission_behind_layout": 0,
    })
    try:
        _normalise_outer_comb_assertion(false_offender_summary)
    except CombRefereeScopeError:
        pass
    else:
        failures.append(
            "offender-derived audit counters/inventory must fail closed")

    owner_certificate = {
        "criterion": "exact-reviewed-layout-comb-subject-owner-v1",
        "valid": True,
        "layout_sha256": snapshot["layout_bindings"]["fixture-1"][
            "layout_sha256"],
        "page": 1,
        "cell_id": "p1c1",
        "legacy_cell_id": "p1c1",
        "subject_key": "p1@0,0,10,10",
        "legacy_bbox": ["0", "0", "10", "10"],
        "bbox_number_format": "canonical-decimal-string-v1",
        "state": "active_resolved",
        "supplies_topology": False,
    }
    normal_owner_offender = {
        "cell": "p1c1", "page": 1, "slots": 2, "latticed": 2,
        "printed": 3, "printed_divider_x": [3.0, 7.0],
        "physical_slots": 2, "declared_slots": 2,
        "emitted_occurrences": 1,
        "slot_indexes": [0, 1], "input_slot_indexes": [0, 1],
        "slot_geometry": [],
        "emission_container_binding": {},
        "emission_layout_position": {},
        "emission_layout_outer_position": {},
        "emission_source_position": {},
        "emission_source_outer_position": {},
        "layout_source_outer_position": {},
        "source_frame_geometry": None,
        "source_owner_certificate": owner_certificate,
        "emission_state": "physical-slots",
        "layout_relation": "mismatch",
        "emission_relation": "mismatch-printed",
        "failure_kinds": ["layout-printed-mismatch"],
        "why": "synthetic source/layout mismatch",
    }
    owner_assertion = {
        **{
            key: outer_assertion_relation[key]
            for key in AUDIT_ASSERTION_SUMMARY_KEYS
        },
        "holds": False,
        "reason": "one mismatch",
        "offender_count": 1,
        "offenders_published": 1,
        "offenders_omitted": 0,
        "offenders_complete": True,
        "offenders": [normal_owner_offender],
        "layout_mismatches": 1,
    }
    try:
        _normalise_outer_comb_assertion(
            owner_assertion, snapshot["layout_bindings"]["fixture-1"])
    except CombRefereeScopeError as error:
        failures.append(
            f"exact layout-bound owner certificate must validate: {error}")
    invalid_physical_offender = clone(normal_owner_offender)
    invalid_physical_offender.update({
        "emission_state": "invalid-slot-geometry",
        "emission_relation": "invalid",
        # The source/layout mismatch remains real, but differing emitted/source
        # counts are not comparable while physical slot geometry is invalid.
        "failure_kinds": ["layout-printed-mismatch", "invalid-emission"],
        "why": (
            "synthetic source/layout mismatch and invalid physical slots"),
    })
    invalid_physical_assertion = clone(owner_assertion)
    invalid_physical_assertion.update({
        "offenders": [invalid_physical_offender],
        "emission_behind_layout": 1,
        "emission_invalid": 1,
        "decided_by_review": 0,
        "decided_by_review_subjects": [],
    })
    try:
        _normalise_outer_comb_assertion(
            invalid_physical_assertion,
            snapshot["layout_bindings"]["fixture-1"])
    except CombRefereeScopeError as error:
        failures.append(
            "invalid physical geometry must not require emitted/source "
            f"mismatch kinds: {error}")
    owner_mutations: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
        ("valid", lambda value: value.update({"valid": False})),
        ("supplies_topology", lambda value: value.update({
            "supplies_topology": True})),
        ("layout_sha256", lambda value: value.update({
            "layout_sha256": "f" * 64})),
        ("page", lambda value: value.update({"page": 2})),
        ("cell_id", lambda value: value.update({"cell_id": "p1c9"})),
        ("legacy_cell_id", lambda value: value.update({
            "legacy_cell_id": "p1c9"})),
        ("subject_key", lambda value: value.update({
            "subject_key": "p1@1,0,11,10"})),
        ("legacy_bbox", lambda value: value.update({
            "legacy_bbox": ["1", "0", "11", "10"]})),
        ("state", lambda value: value.update({
            "state": "active_unresolved"})),
    ]
    for label, mutator in owner_mutations:
        mutated = clone(owner_assertion)
        mutator(mutated["offenders"][0]["source_owner_certificate"])
        try:
            _normalise_outer_comb_assertion(
                mutated, snapshot["layout_bindings"]["fixture-1"])
        except CombRefereeScopeError:
            pass
        else:
            failures.append(
                f"mutated owner-certificate {label} must fail closed")
    owner_count_mutation = clone(owner_assertion)
    owner_count_mutation.update({
        "owner_certificates_valid": 0,
        "owner_certificates_invalid": 1,
    })
    try:
        _normalise_outer_comb_assertion(
            owner_count_mutation,
            snapshot["layout_bindings"]["fixture-1"])
    except CombRefereeScopeError:
        pass
    else:
        failures.append("false owner-certificate summary must fail closed")
    false_source_partition = clone(owner_assertion)
    false_source_partition.update({
        "source_u_frame_evaluable": 1,
        "source_certified_unframed_evaluable": 1,
    })
    try:
        _normalise_outer_comb_assertion(
            false_source_partition,
            snapshot["layout_bindings"]["fixture-1"])
    except CombRefereeScopeError:
        pass
    else:
        failures.append(
            "source frame/unframed counts must partition evaluable subjects")
    false_source_classification = clone(owner_assertion)
    false_source_classification.update({
        "source_u_frame_evaluable": 1,
        "source_certified_unframed_evaluable": 0,
    })
    try:
        _normalise_outer_comb_assertion(
            false_source_classification,
            snapshot["layout_bindings"]["fixture-1"])
    except CombRefereeScopeError:
        pass
    else:
        failures.append(
            "published certified-unframed evidence must bind its counter")
    two_cell_binding = clone(snapshot["layout_bindings"]["fixture-1"])
    second_projection = clone(two_cell_binding["cells"]["p1c1"])
    second_projection.update({
        "cell": "p1c2", "legacy_cell_id": "p1c2", "cell_id": "p1c2",
        "subject_key": "p1@10,0,20,10",
        "bbox": [10.0, 0.0, 20.0, 10.0],
    })
    two_cell_binding["cells"]["p1c2"] = second_projection
    two_cell_binding["audit_expected_ids"] = ["p1c1", "p1c2"]
    second_owner_offender = clone(normal_owner_offender)
    second_owner_offender.update({"cell": "p1c2"})
    second_owner_offender["source_owner_certificate"].update({
        "cell_id": "p1c2", "legacy_cell_id": "p1c2",
        "subject_key": "p1@10,0,20,10",
        "legacy_bbox": ["10", "0", "20", "10"],
    })
    two_valid_assertion = clone(owner_assertion)
    two_valid_assertion.update({
        "combs_expected": 2,
        "combs_checked": 2,
        "expected_comb_ids": ["p1c1", "p1c2"],
        "checked_comb_ids": ["p1c1", "p1c2"],
        "emitted_comb_ids": ["p1c1", "p1c2"],
        "owner_certificates_valid": 2,
        "owner_certificates_invalid": 0,
        "source_u_frame_evaluable": 0,
        "source_certified_unframed_evaluable": 2,
        "layout_mismatches": 2,
        "offender_count": 2,
        "offenders_published": 2,
        "offenders": [normal_owner_offender, second_owner_offender],
    })
    try:
        _normalise_outer_comb_assertion(two_valid_assertion, two_cell_binding)
    except CombRefereeScopeError as error:
        failures.append(f"two exact valid owner certificates must bind: {error}")
    false_two_valid = clone(two_valid_assertion)
    false_two_valid.update({
        "owner_certificates_valid": 1,
        "owner_certificates_invalid": 1,
    })
    try:
        _normalise_outer_comb_assertion(false_two_valid, two_cell_binding)
    except CombRefereeScopeError:
        pass
    else:
        failures.append("two valid certificates cannot be summarized as 1/1")
    two_invalid = clone(two_valid_assertion)
    invalid_certificate = {
        "criterion": "exact-reviewed-layout-comb-subject-owner-v1",
        "valid": False,
        "reason": "synthetic invalid owner",
        "supplies_topology": False,
    }
    for offender in two_invalid["offenders"]:
        offender["source_owner_certificate"] = clone(invalid_certificate)
        offender.update({
            "printed": None,
            "printed_divider_x": [],
            "layout_relation": "unevaluable",
            "emission_relation": "source-unevaluable",
            "failure_kinds": ["source-topology-unevaluable"],
            "why": "synthetic source topology is unevaluable",
        })
    two_invalid.update({
        "owner_certificates_valid": 0,
        "owner_certificates_invalid": 2,
        "source_u_frame_evaluable": 0,
        "source_certified_unframed_evaluable": 0,
        "layout_mismatches": 0,
        "layout_unevaluable": 2,
    })
    try:
        _normalise_outer_comb_assertion(two_invalid)
    except CombRefereeScopeError as error:
        failures.append(f"two explicit invalid owner certificates must bind: {error}")
    false_two_invalid = clone(two_invalid)
    false_two_invalid.update({
        "owner_certificates_valid": 1,
        "owner_certificates_invalid": 1,
    })
    try:
        _normalise_outer_comb_assertion(false_two_invalid)
    except CombRefereeScopeError:
        pass
    else:
        failures.append("two invalid certificates cannot be summarized as 1/1")

    repeated_failure = repeat_run_failure(
        [0, 0], [raw_payload, raw_payload + b"x"])
    if (repeated_failure is None
            or repeated_failure.verdict is not Verdict.UNEVALUABLE):
        failures.append(
            "byte-different repeated referee output must be UNEVALUABLE")

    scope_trees = {
        name: {"sha256": sha256_bytes(name.encode("utf-8"))}
        for name in {"forms", *COMB_REFEREE_ARTIFACT_TREES}
    }
    scope_batch = {"sha256": sha256_bytes(b"batch")}
    base_scope = compose_generated_scope(scope_trees, scope_batch)
    for tree_name in scope_trees:
        mutated_trees = clone(scope_trees)
        mutated_trees[tree_name]["sha256"] = "f" * 64
        if (compose_generated_scope(mutated_trees, scope_batch)["sha256"]
                == base_scope["sha256"]):
            failures.append(
                f"determinism digest ignores generated tree: {tree_name}")
    mutated_batch = clone(scope_batch)
    mutated_batch["sha256"] = "0" * 64
    if (compose_generated_scope(scope_trees, mutated_batch)["sha256"]
            == base_scope["sha256"]):
        failures.append("determinism digest ignores the batch report")

    with tempfile.TemporaryDirectory(
            prefix="formgen-gate-pipeline-self-test-") as pipeline_tmp:
        pipeline_root = pathlib.Path(pipeline_tmp)
        test_slugs = frozenset({"fixture-1"})
        test_inventory = {"fixture-1": True}
        batch_payload = (json.dumps([
            _synthetic_batch_record("fixture-1")], indent=2) + "\n")

        def write_batch(args: list[str]) -> None:
            output = pathlib.Path(args[args.index("--report") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(batch_payload, encoding="utf-8")

        ordering: list[str] = []

        def ordered_runner(args: list[str], _timeout: int) -> tuple[int, str]:
            ordering.append(pathlib.Path(args[0]).name)
            write_batch(args)
            return 0, ""

        def ordered_referee() -> Result:
            ordering.append("referee")
            return Result("comb-referee", Verdict.UNEVALUABLE, "synthetic")

        def ordered_audit() -> Result:
            ordering.append("audit.py")
            return Result("audit-refresh", Verdict.PASS, "synthetic")

        same_generation = {"sha256": "b" * 64, "scope": "same"}
        same_audit = {"path": "build/audit.json", "sha256": "a" * 64}
        ordered_refresh = refresh_full_pipeline(
            runner=ordered_runner,
            generation_reader=lambda _batch: clone(same_generation),
            audit_refresher=ordered_audit,
            referee_refresher=ordered_referee,
            scratch_root=pipeline_root,
            batch_target=pipeline_root / "published-batch.json",
            expected_slugs=test_slugs,
            expected_inventory=test_inventory,
            audit_identity_reader=lambda: clone(same_audit),
        )
        if ordering != ["batch.py", "batch.py", "audit.py", "referee"]:
            failures.append(
                "full refresh must order batch, batch, audit, referee exactly")
        if ordered_refresh.determinism.verdict is not Verdict.PASS:
            failures.append("identical pre-audit generations must pass determinism")

        flipped_order: list[str] = []
        flipped_record = _synthetic_batch_record("fixture-1")
        flipped_record["in_corpus"] = False

        def flipped_runner(args: list[str], _timeout: int) -> tuple[int, str]:
            flipped_order.append(pathlib.Path(args[0]).name)
            output = pathlib.Path(args[args.index("--report") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps([flipped_record]) + "\n", encoding="utf-8")
            return 0, ""

        flipped_refresh = refresh_full_pipeline(
            runner=flipped_runner,
            generation_reader=lambda _batch: clone(same_generation),
            audit_refresher=lambda: (
                flipped_order.append("audit.py")
                or Result("audit-refresh", Verdict.PASS, "must not run")),
            referee_refresher=lambda: (
                flipped_order.append("referee")
                or Result("comb-referee", Verdict.PASS, "must not run")),
            scratch_root=pipeline_root,
            batch_target=pipeline_root / "flipped-batch.json",
            expected_slugs=test_slugs,
            expected_inventory=test_inventory,
        )
        if (flipped_refresh.determinism.verdict is Verdict.PASS
                or flipped_order != ["batch.py"]):
            failures.append(
                "full pipeline must reject a flipped form-root classification")

        changed_order: list[str] = []
        changed_generations = iter([
            {"sha256": "c" * 64, "scope": "first"},
            {"sha256": "d" * 64, "scope": "second"},
        ])

        def changed_runner(args: list[str], _timeout: int) -> tuple[int, str]:
            changed_order.append(pathlib.Path(args[0]).name)
            write_batch(args)
            return 0, ""

        changed_refresh = refresh_full_pipeline(
            runner=changed_runner,
            generation_reader=lambda _batch: next(changed_generations),
            audit_refresher=lambda: (
                changed_order.append("audit.py")
                or Result("audit-refresh", Verdict.PASS, "must not run")),
            referee_refresher=lambda: (
                changed_order.append("referee")
                or Result("comb-referee", Verdict.PASS, "must not run")),
            scratch_root=pipeline_root,
            batch_target=pipeline_root / "changed-batch.json",
            expected_slugs=test_slugs,
            expected_inventory=test_inventory,
        )
        if (changed_refresh.determinism.verdict is not Verdict.FAIL
                or changed_order != ["batch.py", "batch.py"]):
            failures.append(
                "nondeterminism must suppress audit/referee after batch #2")

        stale_order: list[str] = []
        stale_refresh = refresh_full_pipeline(
            runner=lambda args, _timeout: (
                stale_order.append(pathlib.Path(args[0]).name) or 0, ""),
            generation_reader=lambda _batch: clone(same_generation),
            audit_refresher=lambda: (
                stale_order.append("audit.py")
                or Result("audit-refresh", Verdict.PASS, "must not run")),
            referee_refresher=lambda: (
                stale_order.append("referee")
                or Result("comb-referee", Verdict.PASS, "must not run")),
            scratch_root=pipeline_root,
            batch_target=pipeline_root / "stale-batch.json",
            expected_slugs=test_slugs,
            expected_inventory=test_inventory,
        )
        if (stale_refresh.determinism.verdict is Verdict.PASS
                or stale_order != ["batch.py"]):
            failures.append(
                "exit-0 batch without a fresh private report must fail closed")

        audit_failure_order: list[str] = []

        def audit_failure_runner(
                args: list[str], _timeout: int) -> tuple[int, str]:
            audit_failure_order.append("batch.py")
            write_batch(args)
            return 0, ""

        def failed_audit() -> Result:
            audit_failure_order.append("audit.py")
            return Result(
                "audit-refresh", Verdict.UNEVALUABLE, "synthetic failure")

        audit_failure_refresh = refresh_full_pipeline(
            runner=audit_failure_runner,
            generation_reader=lambda _batch: clone(same_generation),
            audit_refresher=failed_audit,
            referee_refresher=lambda: (
                audit_failure_order.append("referee")
                or Result("comb-referee", Verdict.PASS, "must not run")),
            scratch_root=pipeline_root,
            batch_target=pipeline_root / "audit-failure-batch.json",
            expected_slugs=test_slugs,
            expected_inventory=test_inventory,
        )
        if (audit_failure_refresh.audit_refresh.verdict
                is not Verdict.UNEVALUABLE
                or audit_failure_refresh.comb_referee.verdict
                is not Verdict.UNEVALUABLE
                or audit_failure_order
                != ["batch.py", "batch.py", "audit.py"]):
            failures.append(
                "a failed final audit must fail the gate and suppress referee")

        post_audit_order: list[str] = []
        post_audit_generations = iter([
            clone(same_generation), clone(same_generation),
            clone(same_generation),
            {"sha256": "d" * 64, "scope": "audit-mutated"},
        ])

        def post_audit_runner(
                args: list[str], _timeout: int) -> tuple[int, str]:
            post_audit_order.append("batch.py")
            write_batch(args)
            return 0, ""

        post_audit_refresh = refresh_full_pipeline(
            runner=post_audit_runner,
            generation_reader=lambda _batch: next(post_audit_generations),
            audit_refresher=lambda: (
                post_audit_order.append("audit.py")
                or Result("audit-refresh", Verdict.PASS, "synthetic")),
            referee_refresher=lambda: (
                post_audit_order.append("referee")
                or Result("comb-referee", Verdict.PASS, "must not run")),
            scratch_root=pipeline_root,
            batch_target=pipeline_root / "post-audit-batch.json",
            expected_slugs=test_slugs,
            expected_inventory=test_inventory,
        )
        if (post_audit_refresh.determinism.verdict is not Verdict.FAIL
                or post_audit_order != ["batch.py", "batch.py", "audit.py"]):
            failures.append(
                "an audit mutation must invalidate scope and suppress referee")

        final_order: list[str] = []
        final_result = refresh_final_comb_referee(
            compose_final_referee_scope(same_generation, same_audit),
            referee_refresher=lambda: (
                final_order.append("referee")
                or Result("comb-referee", Verdict.PASS, "must not run")),
            generation_reader=lambda _batch: {
                "sha256": "f" * 64, "scope": "changed-after-checks"},
            batch_target=pipeline_root / "published-batch.json",
            expected_slugs=test_slugs,
            expected_inventory=test_inventory,
            audit_identity_reader=lambda: clone(same_audit),
        )
        if (final_result.verdict is not Verdict.UNEVALUABLE or final_order):
            failures.append(
                "final scope mutation must suppress the last referee execution")
        final_audit_order: list[str] = []
        final_audit_result = refresh_final_comb_referee(
            compose_final_referee_scope(same_generation, same_audit),
            referee_refresher=lambda: (
                final_audit_order.append("referee")
                or Result("comb-referee", Verdict.PASS, "must not run")),
            generation_reader=lambda _batch: clone(same_generation),
            batch_target=pipeline_root / "published-batch.json",
            expected_slugs=test_slugs,
            expected_inventory=test_inventory,
            audit_identity_reader=lambda: {
                "path": "build/audit.json", "sha256": "0" * 64},
        )
        if (final_audit_result.verdict is not Verdict.UNEVALUABLE
                or final_audit_order):
            failures.append(
                "final audit mutation must suppress the last referee execution")

        duplicate_batch = [
            _synthetic_batch_record("fixture-1"),
            _synthetic_batch_record("fixture-1"),
        ]
        if not batch_report_errors(duplicate_batch, test_slugs):
            failures.append("duplicate batch-report slugs must fail closed")
        malformed_batch = [_synthetic_batch_record("fixture-1")]
        malformed_batch[0]["sources"] = None
        try:
            malformed_batch_errors = batch_report_errors(
                malformed_batch, test_slugs)
        except Exception as error:  # noqa: BLE001 - regression probe
            failures.append(
                "malformed batch sources must not raise: "
                f"{type(error).__name__}: {error}")
        else:
            if not malformed_batch_errors:
                failures.append("malformed batch sources must fail closed")
        incomplete_batch = [_synthetic_batch_record("fixture-1")]
        incomplete_batch[0].update({
            "sources": [*incomplete_batch[0]["sources"], None],
            "pages": 0, "html_bytes": 0, "html": None, "paper": "",
            "page_papers": [],
            "guide_detected": {}, "guide_build": {}, "fonts": [None],
            "font_plans": [], "asset_digests": {},
        })
        try:
            incomplete_batch_errors = batch_report_errors(
                incomplete_batch, test_slugs)
        except Exception as error:  # noqa: BLE001 - regression probe
            failures.append(
                "incomplete nested batch evidence must not raise: "
                f"{type(error).__name__}: {error}")
        else:
            if not incomplete_batch_errors:
                failures.append(
                    "incomplete nested batch evidence must fail closed")

    disagreement = clone(report)
    disagreement["totals"]["comparisons"]["agree"] = 0
    disagreement["totals"]["comparisons"]["repair-lattice"] = 1
    if _comb_referee_outcome(
            disagreement, {"pending_transitions": 0}).verdict is not Verdict.FAIL:
        failures.append("an actual referee disagreement must be FAIL, not UNEVALUABLE")

    command = _comb_referee_command(
        pathlib.Path("/tmp/private-report.json"),
        pathlib.Path("/tmp/private-empty-pycache"))
    if command[:4] != [sys.executable, "-I", "-S", "-B"]:
        failures.append(
            "comb referee must use exact sys.executable with -I -S -B")
    environment_probe = {
        "PATH": "/poison", "PYTHONPATH": "poison", "PYTHONHOME": "poison",
    }
    sanitized = _sanitized_referee_environment(snapshot, environment_probe)
    if "PYTHONPATH" in sanitized or "PYTHONHOME" in sanitized:
        failures.append("comb referee environment must remove Python path/home")

    # A comb's expected emission band is the WRITING surface, never the guide
    # tick.  Both this gate and comb_referee.py re-derive that band from the
    # same layout independently, and for a time both read comb["y0"]/["y1"] --
    # the ~2.88pt tick the source paints at the cell's foot -- while emit
    # rendered comb["writing_y0"]/["writing_y1"].  Nothing caught it: the two
    # producers agreed with each other, and neither self-test named the field.
    # The geometry below is 0605-1999 p1c3 verbatim, where the two bands are
    # 15.34pt apart, so reading the wrong one cannot round to the right one.
    #
    # The same holds on the OTHER axis and is the same trap: `slot_x`'s outer
    # values are the rails' CENTRES (82.31 / 109.08) and the compartments are
    # laid on those rails' ink edges (82.69 / 108.71), 0.38pt and 0.37pt
    # further in -- half the printed wall, which is where this sheet prints the
    # `)` of "(  MM / YYYY )".  Every internal edge stays the measured divider.
    band_cell = {
        "comb": {
            "slot_x": [82.31, 95.64, 109.08],
            "y0": 165.6, "y1": 168.48,
            "writing_y0": 150.26, "writing_y1": 167.72,
            "writing_x0": 82.69, "writing_x1": 108.71,
        },
    }
    band_box = {"x0": 82.31, "y0": 149.51, "x1": 109.08, "y1": 168.47}
    band_slots = _emission_geometry_from_layout(1, band_cell, band_box)["slots"]
    if (len(band_slots) != 2
            or any(abs(slot["top"] - 0.75) > 1e-9
                   or abs(slot["height"] - 17.46) > 1e-9
                   for slot in band_slots)):
        failures.append(
            "expected comb emission geometry must follow the writing band")
    if any(abs(slot["top"] - 16.09) <= 1e-6
           or abs(slot["height"] - 2.88) <= 1e-6 for slot in band_slots):
        failures.append(
            "expected comb emission geometry must not follow the guide tick")
    if (len(band_slots) != 2
            or abs(band_slots[0]["left"] - 0.38) > 1e-9
            or abs(band_slots[0]["width"] - 12.95) > 1e-9
            or abs(band_slots[1]["left"] - 13.33) > 1e-9
            or abs(band_slots[1]["width"] - 13.07) > 1e-9):
        failures.append(
            "expected comb emission geometry must follow the writing edges")
    if any(abs(slot["left"]) <= 1e-6 for slot in band_slots):
        failures.append(
            "expected comb emission geometry must not follow the rail centres")
    for absent in ("writing_y0", "writing_y1", "writing_x0", "writing_x1"):
        starved = {"comb": {
            name: value for name, value in band_cell["comb"].items()
            if name != absent}}
        try:
            _emission_geometry_from_layout(1, starved, band_box)
        except KeyError:
            pass
        else:
            failures.append(
                f"a layout comb missing {absent} must fail closed")

    with tempfile.TemporaryDirectory(prefix="formgen-gate-self-test-") as tmp:
        root = pathlib.Path(tmp)
        audit_scope_fixture = {
            key: clone(value) for key, value in snapshot.items()
            if key != "audit"
        }
        audit_scope_reader = lambda: clone(audit_scope_fixture)
        target = root / "audit.json"
        target.write_text('[{"stale": true}]\n', encoding="utf-8")
        assertion_slugs = frozenset({"fixture-1"})

        refresh_failure = refresh_assertions_report(
            target=target,
            scratch_root=root,
            runner=lambda _args, _timeout: (1, "synthetic refresh failure"),
            expected_slugs=assertion_slugs,
        )
        if (refresh_failure is None
                or refresh_failure.verdict is not Verdict.UNEVALUABLE):
            failures.append("a failed assertion refresh must be UNEVALUABLE")
        if load(target) != [{"stale": True}]:
            failures.append("a failed assertion refresh must not publish partial data")

        def invalid_refresh(args: list[str], _timeout: int) -> tuple[int, str]:
            out = pathlib.Path(args[args.index("--out") + 1])
            out.write_text('[{"fresh": true}]\n', encoding="utf-8")
            return 0, ""

        invalid_refresh_result = refresh_assertions_report(
            target=target,
            scratch_root=root,
            runner=invalid_refresh,
            expected_slugs=assertion_slugs,
        )
        if (invalid_refresh_result is None
                or invalid_refresh_result.verdict is not Verdict.UNEVALUABLE
                or load(target) != [{"stale": True}]):
            failures.append(
                "an invalid exit-0 assertion refresh must preserve prior data")

        valid_assertion_records = [_synthetic_audit_record("fixture-1")]

        def valid_refresh(args: list[str], _timeout: int) -> tuple[int, str]:
            out = pathlib.Path(args[args.index("--out") + 1])
            out.write_text(
                json.dumps(valid_assertion_records) + "\n", encoding="utf-8")
            return 0, ""

        refresh_success = refresh_assertions_report(
            target=target, scratch_root=root, runner=valid_refresh,
            expected_slugs=assertion_slugs)
        if (refresh_success is not None
                or load(target) != valid_assertion_records):
            failures.append("a valid assertion refresh must publish fresh data")

        audit_slugs = frozenset(
            f"fixture-{index}" for index in range(EXPECTED_FORMS))
        slug_only_records = [
            {"slug": f"fixture-{index}"}
            for index in range(EXPECTED_FORMS)
        ]
        if not full_audit_payload_errors(slug_only_records, audit_slugs):
            failures.append(
                "a slug-only full-audit payload must fail closed")

        audit_records = [
            _synthetic_audit_record(f"fixture-{index}")
            for index in range(EXPECTED_FORMS)
        ]
        payload_errors = full_audit_payload_errors(
            audit_records, audit_slugs)
        if payload_errors:
            failures.append(
                "a complete synthetic full-audit payload must validate: "
                + "; ".join(payload_errors[:3]))
        huge_denominators = clone(audit_records)
        huge_denominators[0].update({
            "rules_ref": 10 ** 10000, "text_ref": 10 ** 10000,
        })
        try:
            huge_denominator_errors = full_audit_payload_errors(
                huge_denominators, audit_slugs)
        except Exception as error:  # noqa: BLE001 - hostile JSON probe
            failures.append(
                "huge integer denominators must not raise: "
                f"{type(error).__name__}: {error}")
        else:
            if huge_denominator_errors:
                failures.append(
                    "valid huge integer denominators must remain evaluable: "
                    + "; ".join(huge_denominator_errors[:3]))
        for label, mutator in (
                (
                    "error status",
                    lambda value: value[0].update({
                        "status": "error", "error": "synthetic"}),
                ),
                (
                    "missing metric",
                    lambda value: value[0].pop("rules_missing"),
                ),
                (
                    "missing metric denominator",
                    lambda value: value[0].pop("rules_ref"),
                ),
                (
                    "failed provenance",
                    lambda value: value[0]["provenance_validation"].update({
                        "validated_after": False}),
                ),
                (
                    "incomplete render manifest",
                    lambda value: value[0]["input_manifest"]["render"].update({
                        "complete": False}),
                ),
                (
                    "empty nested input manifest",
                    lambda value: value[0]["input_manifest"].update({
                        "schema": 1, "producer": {}, "runtime": {},
                        "inputs": {}}),
                ),
                (
                    "null input manifest",
                    lambda value: value[0].__setitem__(
                        "input_manifest", None),
                ),
                (
                    "list input manifest",
                    lambda value: value[0].__setitem__(
                        "input_manifest", []),
                ),
                (
                    "null render dependency inventory",
                    lambda value: value[0]["input_manifest"]["render"].update({
                        "dependencies": None}),
                ),
                (
                    "huge runtime member byte count",
                    lambda value: value[0]["input_manifest"]["runtime"]
                    ["loaded_application_files"]["members"][0].update({
                        "bytes": 10 ** 10000}),
                ),
                (
                    "fabricated assertion detail",
                    lambda value: value[0]["assertions"].update({
                        "inputs_over_printed_text": {"holds": True}}),
                ),
                (
                    "hostile render kinds",
                    lambda value: value[0]["input_manifest"]["render"].update({
                        "dependencies": [{
                            "path": "asset.png", "mime_type": "image/png",
                            "present": True, "bytes": 1,
                            "sha256": "1" * 64, "kinds": [{}],
                            "referrers": ["fixture-0.html"],
                        }]}),
                ),
                (
                    "malformed comb detail",
                    lambda value: value[0]["assertions"].update({
                        "comb_slots_match_printed": []}),
                ),
                (
                    "malformed comb reason",
                    lambda value: value[0]["assertions"]
                    ["comb_slots_match_printed"].update({"reason": []}),
                ),
                (
                    "boolean comb counts",
                    lambda value: value[0]["assertions"]
                    ["comb_slots_match_printed"].update({
                        "combs_expected": False, "combs_checked": False}),
                ),
                (
                    "unsupported comb field",
                    lambda value: value[0]["assertions"]
                    ["comb_slots_match_printed"].update({"invented": 1}),
                ),
                (
                    "overflowing percentage",
                    lambda value: value[0].update({
                        "rules_pct": 10 ** 1000}),
                )):
            malformed_records = clone(audit_records)
            mutator(malformed_records)
            if not full_audit_payload_errors(
                    malformed_records, audit_slugs):
                failures.append(
                    f"full audit must reject {label} evidence")
        bound_audit_records = [
            _synthetic_audit_record("fixture-1", snapshot)]
        baseline_binding_errors = audit_payload_snapshot_binding_errors(
            bound_audit_records, audit_scope_fixture)
        if baseline_binding_errors:
            failures.append(
                "complete synthetic audit/snapshot binding must validate: "
                + "; ".join(baseline_binding_errors[:3]))

        incomplete_requests = clone(bound_audit_records)
        entrypoint = incomplete_requests[0]["input_manifest"]["render"][
            "entrypoint"]
        incomplete_requests[0]["render_requests"].update({
            "fulfilled": [entrypoint], "fulfilled_requests": 1,
        })
        if (not full_audit_payload_errors(
                incomplete_requests, frozenset({"fixture-1"}))
                or not audit_payload_snapshot_binding_errors(
                    incomplete_requests, audit_scope_fixture)):
            failures.append(
                "roundtrip request evidence must exhaust the retained closure")

        for label, mutator in (
                ("producer digest", lambda value: value[0]["input_manifest"]
                 ["producer"].update({"sha256": "f" * 64})),
                ("PyMuPDF version", lambda value: value[0]["input_manifest"]
                 ["runtime"]["pymupdf"].update({
                     "package_version": "invented",
                     "version_bind": "invented"})),
                ("IR input digest", lambda value: value[0]["input_manifest"]
                 ["inputs"]["ir"].update({"sha256": "e" * 64})),
                ("source selected path", lambda value: value[0]
                 ["input_manifest"]["inputs"]["source_pdf"].update({
                     "path": "other.pdf"})),
                ("render dependency subset", lambda value: value[0]
                 ["input_manifest"]["render"].update({"dependencies": []})),
                ("Playwright closure", lambda value: value[0]
                 ["roundtrip_runtime"]["dependency_closure"].update({
                     "tree_sha256": "d" * 64})),
                ("Chromium executable", lambda value: value[0]
                 ["roundtrip_runtime"]["chromium"].update({
                     "sha256": "c" * 64})),
                ("Chromium live version", lambda value: value[0]
                 ["roundtrip_runtime"].update({
                     "live_browser_version": "x"})),
                ):
            mutated_records = clone(bound_audit_records)
            mutator(mutated_records)
            if not audit_payload_snapshot_binding_errors(
                    mutated_records, audit_scope_fixture):
                failures.append(
                    f"mutated audit {label} must fail snapshot binding")

        absent_guide_scope = clone(audit_scope_fixture)
        retained_html = [
            item for item in absent_guide_scope["artifact_trees"]["html"]["files"]
            if item["path"] != "build/html/fixture-1.guide.html"]
        absent_guide_scope["artifact_trees"]["html"] = {
            **_file_manifest(retained_html), "root": "build/html"}
        absent_guide_records = clone(bound_audit_records)
        absent_guide_records[0]["input_manifest"]["inputs"]["guide_html"] = {
            "file": "transplanted.guide.html", "required": False,
            "present": False, "bytes": None, "sha256": None,
        }
        if not audit_payload_snapshot_binding_errors(
                absent_guide_records, absent_guide_scope):
            failures.append(
                "absent optional guide HTML must still bind its filename")

        hostile_scope = clone(audit_scope_fixture)
        hostile_scope["source_pdfs"]["relations"] = None
        if not audit_payload_snapshot_binding_errors(
                bound_audit_records, hostile_scope):
            failures.append("hostile outer source relations must fail closed")

        fake_module_scope = clone(audit_scope_fixture)
        fake_member = {
            "path": "pymupdf/fake/not-a-module.dat", "bytes": 7,
            "sha256": sha256_bytes(b"fake-module"),
        }
        projection = fake_module_scope["runtime"]["python_dependency_files"]
        projection["members"].append(fake_member)
        projection["members"].sort(key=lambda item: item["path"])
        projection["files"] = len(projection["members"])
        projection["bytes"] = sum(
            item["bytes"] for item in projection["members"])
        projection["sha256"] = canonical_digest(projection["members"])
        fake_module_records = clone(bound_audit_records)
        loaded = fake_module_records[0]["input_manifest"]["runtime"][
            "loaded_application_files"]
        loaded["members"].append({
            "file": "module/pymupdf.fake", "bytes": fake_member["bytes"],
            "sha256": fake_member["sha256"],
        })
        loaded["members"].sort(key=lambda item: item["file"])
        runtime_tuples = [
            (item["file"], item["bytes"], item["sha256"])
            for item in loaded["members"]]
        loaded["files"] = len(runtime_tuples)
        loaded["bytes"] = sum(item[1] for item in runtime_tuples)
        loaded["tree_sha256"] = sha256_bytes(json.dumps(
            runtime_tuples, separators=(",", ":")).encode("ascii"))
        if not audit_payload_snapshot_binding_errors(
                fake_module_records, fake_module_scope):
            failures.append(
                "runtime module identity must reject arbitrary descendants")

        attested_payload = (
            json.dumps(bound_audit_records, sort_keys=True) + "\n").encode(
                "utf-8")
        dependencies = audit_scope_fixture["runtime"][
            "python_dependencies"]
        pycache_prefix = str(
            pathlib.Path(tempfile.gettempdir()).resolve()
            / ".gate-python-isolated-selftest" / "pycache")
        target_argv = [
            str(HERE / "audit.py"), "--out",
            str((root / ".full-audit-synthetic" / "audit.json").resolve()),
        ]
        synthetic_launcher_receipt = {
            "schema": "formgen-isolated-python-launch-receipt-v2",
            "bootstrap": {
                "schema": (
                    "formgen-isolated-python-bootstrap-receipt-v2"),
                "executable": audit_scope_fixture["runtime"]["python"][
                    "path"],
                "isolated": 1,
                "no_site": 1,
                "dont_write_bytecode": True,
                "pycache_prefix": pycache_prefix,
                "cwd": str(REPO.resolve()),
                "pythonpath_absent": True,
                "pythonhome_absent": True,
                "site_not_loaded": True,
                "bootstrap_sha256": "1" * 64,
                "spec_sha256": "2" * 64,
                "dependency_manifest_sha256": dependencies["sha256"],
                "target_argv_sha256": _compact_digest(target_argv),
                "worker_exit": 0,
                "target_exit": 0,
                "recursive_launcher_installed": True,
                "process_group_supervised": True,
                "subprocess_popen_python_rewrite_installed": True,
                "os_process_control_guards_installed": True,
                "lingering_descendants_detected": False,
                "cleanup_complete": True,
            },
            "dependency_manifest": clone(dependencies),
            "command_flags": list(ISOLATED_PYTHON_ATTESTED_FLAGS),
            "pythonpath_removed": True,
            "pythonhome_removed": True,
            "source_dependencies_copied_from_verified_fds": True,
            "private_dependencies_validated_before_after": True,
            "process_group_supervised": True,
            "subprocess_popen_python_rewrite_installed": True,
            "os_process_control_guards_installed": True,
            "supervised_group_quiescent": True,
            "timed_out": False,
            "cleanup_complete": True,
            "child_exit": 0,
        }
        attested_envelope = compose_audit_application_envelope(
            audit_scope_fixture, attested_payload, len(bound_audit_records),
            synthetic_launcher_receipt, target_argv)
        envelope_errors = validate_audit_application_envelope(
            attested_envelope, attested_payload, audit_scope_fixture)
        if envelope_errors:
            failures.append(
                "fresh audit application envelope must validate: "
                + "; ".join(envelope_errors[:3]))
        transplanted_records = clone(bound_audit_records)
        transplanted_records[0]["candidate_pdf"].update({
            "bytes": 999, "sha256": "a" * 64,
            "candidate_ir_sha256": "b" * 64,
        })
        transplanted_records[0].update({
            "rules_ref": 100, "rules_missing": 99, "rules_pct": 1.0,
            "text_ref": 100, "text_missing": 99, "text_pct": 1.0,
        })
        transplanted_payload = (
            json.dumps(transplanted_records, sort_keys=True) + "\n").encode(
                "utf-8")
        if not validate_audit_application_envelope(
                attested_envelope, transplanted_payload,
                audit_scope_fixture):
            failures.append(
                "post-run candidate/metric transplantation must invalidate "
                "the raw-report envelope")
        for label, mutator in (
                (
                    "audit isolated-Python flags",
                    lambda value: value["invocation"].update({
                        "python_flags": []}),
                ),
                (
                    "audit private output",
                    lambda value: value["invocation"].update({
                        "output": "build/audit.json"}),
                ),
                (
                    "audit target argv",
                    lambda value: value["invocation"].update({
                        "target_argv": [str(HERE / "audit.py"), "--out",
                                        str(root / "other" / "audit.json")]}),
                ),
                (
                    "audit raw digest",
                    lambda value: value["raw_report"].update({
                        "sha256": "f" * 64}),
                ),
                (
                    "audit dependency receipt",
                    lambda value: value["invocation"]["launcher_receipt"][
                        "dependency_manifest"].update({"sha256": "e" * 64}),
                ),
                (
                    "audit timeout receipt",
                    lambda value: value["invocation"]["launcher_receipt"].update(
                        {"timed_out": True}),
                ),
                (
                    "audit supervised-group quiescence",
                    lambda value: value["invocation"]["launcher_receipt"].update(
                        {"supervised_group_quiescent": False}),
                ),
                (
                    "audit process-group supervision",
                    lambda value: value["invocation"]["launcher_receipt"].update(
                        {"process_group_supervised": False}),
                )):
            mutated_envelope = clone(attested_envelope)
            mutator(mutated_envelope)
            _resign_for_self_test(mutated_envelope)
            if not validate_audit_application_envelope(
                    mutated_envelope, attested_payload,
                    audit_scope_fixture):
                failures.append(f"mutated {label} must fail closed")
        changed_audit_scope = clone(audit_scope_fixture)
        changed_audit_scope["artifact_trees"]["layout"]["sha256"] = (
            "e" * 64)
        if not validate_audit_application_envelope(
                attested_envelope, attested_payload,
                changed_audit_scope):
            failures.append(
                "post-audit application input mutation must fail closed")

    for name in failures:
        print(f"FAIL {name}", file=sys.stderr)
    print(f"gate self-test: {len(failures)} failure(s)", file=sys.stderr)
    return 1 if failures else 0


def summarise(results: list[Result], echo: bool = False) -> int:
    width = max((len(r.name) for r in results), default=0)
    for r in results:
        if echo:
            print(f"  {r.verdict.value:<11} {r.name:<{width}}  {r.detail}")
    failed = [r for r in results if not r.verdict.ok]
    if echo:
        print()
        if failed:
            print(f"GATE FAILS -- {len(failed)} of {len(results)} checks not satisfied")
            for r in failed:
                print(f"  - {r.name}: {r.detail}")
        else:
            print(f"GATE PASSES -- all {len(results)} checks satisfied")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", action="append", choices=sorted(CHECKS) + ["determinism"],
                        help="Run one check while iterating. Not the done-condition.")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--skip-regenerate", action="store_true",
                        help="Score the forms/ tree as it stands. Not the done-condition.")
    parser.add_argument("--json", type=pathlib.Path, default=None)
    args = parser.parse_args(argv)

    if args.list:
        for name in list(CHECKS) + ["determinism"]:
            print(name)
        return 0
    if args.self_test:
        return self_test()

    full = not args.only
    full_refresh = None
    if full and args.json is not None:
        json_output = args.json.resolve()
        for protected_root in (REPO, COMB_REFEREE_SOURCE_ROOT):
            try:
                json_output.relative_to(protected_root.resolve())
            except ValueError:
                continue
            parser.error(
                "the full gate's --json output must be outside the repository "
                "and official-source tree; the write would stale the final "
                "referee snapshot")
    if full and not args.skip_regenerate:
        # Fail fast on a stale GENERATED-AND-TRACKED file. forms/index.html is
        # written by batch.package() as its last act, so if the committed copy
        # is stale the first regeneration dirties the worktree, and
        # capture_audit_application_snapshot then refuses to bind -- fifty
        # minutes later, with six checks cascading to UNEVALUABLE and a message
        # that names neither the file nor the cause. That has now cost three
        # full runs. Say it in five seconds instead, and name the file.
        _pre_state = _git_state()
        if not _pre_state["worktree_clean"]:
            parser.error(
                "the worktree is not clean, so the audit could not bind its "
                "application scope after regeneration. Commit or revert first. "
                "If the only change is a generated file (forms/index.html is "
                "the usual one), regenerate and commit it: "
                "python3 tools/formgen/batch.py --report build/batch-report.json")
        print("regenerating twice and auditing final bytes; referee runs last...",
              file=sys.stderr)
        full_refresh = refresh_full_pipeline(referee_refresher=None)
        _post_state = _git_state()
        if not _post_state["worktree_clean"]:
            parser.error(
                "regeneration changed tracked files, so this tree does not "
                "match its commit and the audit cannot bind. The regenerated "
                "bytes are on disk now -- inspect and commit them, then re-run. "
                "This is what a stale tracked forms/index.html looks like.")
        for diagnostic in full_refresh.diagnostics:
            print(f"  {diagnostic}", file=sys.stderr)

    wanted = args.only or list(CHECKS)
    refresh_failure = None
    if args.only and "assertions" in args.only:
        print("refreshing assertion audit...", file=sys.stderr)
        refresh_failure = refresh_assertions_report()
    def evaluate_check(name: str) -> Result:
        if name == "assertions" and refresh_failure is not None:
            return refresh_failure
        if (full_refresh is not None
                and not full_refresh.determinism.verdict.ok
                and name == "conversion"):
            return Result(
                name, Verdict.UNEVALUABLE,
                "fresh deterministic generation failed; stale batch report "
                "was not scored",
            )
        if (full_refresh is not None
                and not full_refresh.audit_refresh.verdict.ok
                and name in AUDIT_DEPENDENT_CHECKS):
            return Result(
                name, Verdict.UNEVALUABLE,
                "fresh final-corpus audit failed; stale audit was not scored",
            )
        return CHECKS[name]()

    results = [
        evaluate_check(name) for name in wanted
        if name in CHECKS and name != "comb-referee"
    ]
    if full_refresh is not None:
        results.append(full_refresh.audit_refresh)
    if "determinism" in wanted or full:
        results.append(
            full_refresh.determinism if full_refresh is not None
            else check_determinism(regenerate=False))

    # No executable/mutating gate check follows this point. In a full run the
    # current generated scope is re-read immediately before the two isolated
    # referee children, so module self-tests cannot silently stale its evidence.
    if "comb-referee" in wanted:
        print("running final application-scoped comb referee...", file=sys.stderr)
        if full_refresh is not None:
            if (not full_refresh.audit_refresh.verdict.ok
                    or full_refresh.generated_scope is None):
                comb_result = full_refresh.comb_referee
            else:
                comb_result = refresh_final_comb_referee(
                    full_refresh.generated_scope)
        elif args.only and "comb-referee" in args.only:
            comb_result = refresh_comb_referee_report()
        else:
            comb_result = check_comb_referee()
        results.append(comb_result)

    print(f"\nformgen gate -- {len(results)} checks\n")
    exit_code = summarise(results, echo=True)

    if args.json:
        args.json.write_text(json.dumps(
            [{"name": r.name, "verdict": r.verdict.value, "detail": r.detail}
             for r in results], indent=2) + "\n", encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
