#!/usr/bin/env python3
"""Validate the committed forms/ tree using nothing but the committed forms/ tree.

Every other check in this pipeline needs something CI does not have. The six
pinned BIR PDFs are deliberately untracked (`*.pdf` is gitignored; they are
official documents), and `build/` is regenerable intermediates, also gitignored.
So a workflow that runs `gate.py`, `audit.py` or any module's `--self-test`
either fails for want of inputs or, worse, reports success having measured
nothing. This validator is the part of the work that a fresh clone *can*
evaluate, and it guards the thing that actually ships: the 345 tracked files
under `forms/`, which are now maintained by hand.

It reads only files inside the tree. It never opens a PDF, never looks at
`build/`, and never shells out. Same tree in, same bytes out.

What it will NOT do is claim a pass for something it could not evaluate. Two
properties of this tree are genuinely unverifiable without the source PDFs:

  * a bundle's `guides/<name>.pdf` is the pinned official document, so it is not
    tracked and is simply absent in a clone;
  * an asset in the shared pool is named after the sha256 of the *base image
    stream inside the PDF* (see extract.py's `asset_file_name`), and when the
    source declares a soft mask the file on disk is the composite painted over
    white -- different bytes, same name, by design.

Both are reported as NOT-RUN lines with counts and names. They never count as
passes and they are never silently skipped.

Usage:
    python3 tools/formgen/validate_tree.py              # validate forms/
    python3 tools/formgen/validate_tree.py --verbose    # every failure, not the first 12
    python3 tools/formgen/validate_tree.py --self-test  # prove each check can fail
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import shutil
import sys
import tempfile
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent

# What batch.py writes into a bundle. Anything else in there is a hand edit or a
# leftover from a renamed slug, which is exactly what check 7 is looking for.
REQUIRED_BUNDLE_FILES = ("index.html", "form.css", "provenance.json")
GUIDE_FILES = ("guide.html", "guide.css")
GUIDE_PDF_DIR = "guides"
# forms/index.html is written by index_page.py, not by a bundle.
ASSET_MANIFEST = "assets-manifest.json"
ROOT_ENTRIES = {"base.css", "index.html", ASSET_MANIFEST, "fonts", "assets", "extra"}
EXTRA_DIR = "extra"
FONT_DIR = "fonts"
ASSET_DIR = "assets"
SHARED_DIRS = (FONT_DIR, ASSET_DIR)

SEVERITIES = {"blocker", "major", "minor", "cosmetic"}
STATUSES = {"open", "fixed", "not-a-defect", "deferred"}
RESOLVED_STATUSES = {"fixed", "not-a-defect"}
FINDING_KEYS = ("id", "form", "severity", "status", "what", "where", "evidence",
                "resolution")

HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
ASSET_NAME_RE = re.compile(r"^([0-9a-f]{64})\.([a-z0-9]+)$")
PAPER_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)x([0-9]+(?:\.[0-9]+)?)$")
AT_PAGE_RE = re.compile(r"@page\s*\{[^}]*?size:\s*([0-9.]+)pt\s+([0-9.]+)pt")
COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
ATTR_REF_RE = re.compile(r"""\b(?:href|src)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
CSS_URL_RE = re.compile(r"""url\(\s*(?:"([^"]*)"|'([^']*)'|([^)'"\s]*))\s*\)""")
PAGE_DIV_RE = re.compile(r"""<div\b[^>]*\bclass="page\b[^"]*"[^>]*>""")
ATTR_RE = re.compile(r"""\b([a-zA-Z][a-zA-Z0-9-]*)\s*=\s*"([^"]*)\"""")
# Artwork: every <img>/<image>, plus anything carrying emit.py's seal or its
# "I could not write this image" placeholder.
ARTWORK_TAG_RE = re.compile(
    r"""<(?:img|image)\b[^>]*>|<[a-z]+\b[^>]*\bdata-(?:sha256|missing-src)="[^>]*>""")
# A script body is not markup: its text can contain anything, including the
# literal spelling of a tag inside a comment.
SCRIPT_BODY_RE = re.compile(r"<script\b[^>]*>.*?</script\s*>", re.S | re.I)
# ...but a script that BUILDS an asset path would hide a real reference from the
# scan above, so that is looked for explicitly and fails loudly.
ASSET_URL_IN_SCRIPT_RE = re.compile(
    r"<script\b[^>]*>(?:(?!</script).)*?assets/[0-9a-f]{8}", re.S | re.I)
LENGTH_RE = {axis: re.compile(rf"\b{axis}:\s*([0-9.]+)pt") for axis in ("width", "height")}
NON_LOCAL_PREFIXES = ("http:", "https:", "data:", "mailto:", "tel:", "javascript:", "//")

# Declarations inside these at-rules describe one indivisible resource. Hoisting
# `font-display:block` out of an @font-face into base.css would not share
# anything, it would break the rule -- so they are compared whole, never split.
ATOMIC_AT_RULES = ("@font-face", "@page", "@counter-style", "@property")

FILE_MAGIC = {
    "png": (b"\x89PNG\r\n\x1a\n", b"IEND\xaeB`\x82"),
    "jpeg": (b"\xff\xd8\xff", b"\xff\xd9"),
    "jpg": (b"\xff\xd8\xff", b"\xff\xd9"),
    "woff2": (b"wOF2", None),
    "pdf": (b"%PDF-", None),
}

PT_TOLERANCE = 0.01     # paper sizes are written to 2dp; this is exact-match slack


# ---------------------------------------------------------------------------
# the tree
# ---------------------------------------------------------------------------


@dataclass
class Bundle:
    """One form directory, whether or not it is well-formed."""

    rel: str                        # "2551q-2018" or "extra/1600wp-2010"
    path: pathlib.Path
    provenance: dict[str, Any] | None
    provenance_error: str | None

    @property
    def slug(self) -> str:
        return self.path.name

    @property
    def in_extra(self) -> bool:
        return "/" in self.rel

    @property
    def depth(self) -> str:
        """The prefix a reference in this bundle needs to reach the tree root."""
        return "../../" if self.in_extra else "../"

    def has(self, name: str) -> bool:
        return (self.path / name).is_file()

    @property
    def guide_pdfs(self) -> list[str]:
        """The guide PDFs provenance says this bundle links, in recorded order."""
        guide = (self.provenance or {}).get("guide") or {}
        return [entry.get("file", "") for entry in guide.get("standalone_pdfs") or []]


@dataclass
class Tree:
    root: pathlib.Path
    bundles: list[Bundle]
    findings_path: pathlib.Path

    def rel(self, path: pathlib.Path) -> str:
        """A path as it should be named in a failure message.

        Repo-relative in the checkout; root-relative for a tree elsewhere (the
        self-test's fixtures), so a message is never a wall of temp directory.
        """
        for base in (REPO, self.root.resolve().parent):
            try:
                return str(path.resolve().relative_to(base))
            except ValueError:
                continue
        return str(path)

    def documents(self) -> Iterator[tuple[Bundle | None, pathlib.Path]]:
        """Every HTML and CSS file in the tree, bundle files first."""
        for bundle in self.bundles:
            for name in (*REQUIRED_BUNDLE_FILES, *GUIDE_FILES):
                if name.endswith((".html", ".css")) and bundle.has(name):
                    yield bundle, bundle.path / name
        for name in sorted(p.name for p in self.root.iterdir()):
            if name.endswith((".html", ".css")) and (self.root / name).is_file():
                yield None, self.root / name


def load_tree(root: pathlib.Path, findings_path: pathlib.Path) -> Tree:
    """Discover bundles by directory, not by whether they are complete.

    A directory missing `provenance.json` is still a bundle -- skipping it would
    turn the loudest defect into silence.
    """
    bundles: list[Bundle] = []
    candidates: list[pathlib.Path] = []
    for entry in sorted(root.iterdir()):
        if entry.is_dir() and entry.name not in SHARED_DIRS and entry.name != EXTRA_DIR:
            candidates.append(entry)
    extra = root / EXTRA_DIR
    if extra.is_dir():
        candidates += [entry for entry in sorted(extra.iterdir()) if entry.is_dir()]

    for path in candidates:
        rel = str(path.relative_to(root))
        provenance: dict[str, Any] | None = None
        error: str | None = None
        source = path / "provenance.json"
        if not source.is_file():
            error = "provenance.json is missing"
        else:
            try:
                loaded = json.loads(source.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                error = f"provenance.json does not parse: {exc}"
            else:
                if isinstance(loaded, dict):
                    provenance = loaded
                else:
                    error = f"provenance.json is a {type(loaded).__name__}, not an object"
        bundles.append(Bundle(rel=rel, path=path, provenance=provenance,
                              provenance_error=error))
    return Tree(root=root, bundles=bundles, findings_path=findings_path)


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------


@dataclass
class Result:
    """One check's verdict. `notes` is what could not be evaluated, ever."""

    name: str
    headline: str
    failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


# ---------------------------------------------------------------------------
# reference scanning, shared by several checks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Reference:
    document: pathlib.Path
    raw: str                 # exactly as written, still percent-encoded
    target: pathlib.Path     # resolved, percent-decoded, normalised
    path: str                # percent-decoded, relative, no fragment or query


def local_references(document: pathlib.Path, text: str) -> list[Reference]:
    """Every same-tree href/src/url() in one document.

    Percent-decoding happens here and only here. Guide PDFs are named
    "1701Q Guide Jan 2018.pdf", so a check that tests the raw href reports a
    missing file for every one of them -- 12 false failures, which is how this
    was got wrong before.
    """
    raw: list[str] = []
    for match in ATTR_REF_RE.finditer(text):
        raw.append(match.group(1) if match.group(1) is not None else match.group(2))
    for match in CSS_URL_RE.finditer(text):
        raw.append(next(g for g in match.groups() if g is not None))

    out: list[Reference] = []
    seen: set[str] = set()
    for ref in raw:
        if not ref or ref in seen:
            continue
        seen.add(ref)
        if ref.startswith("#") or ref.lower().startswith(NON_LOCAL_PREFIXES):
            continue
        decoded = urllib.parse.unquote(ref.split("#")[0].split("?")[0])
        if not decoded:
            continue
        target = (document.parent / decoded).resolve()
        out.append(Reference(document=document, raw=ref, target=target, path=decoded))
    return sorted(out, key=lambda r: r.raw)


def read_text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------


def split_declarations(body: str) -> list[str]:
    """Split a declaration block on top-level semicolons.

    A naive `body.split(";")` cuts inside `url(data:...;base64,...)` and inside
    quoted font names, which would invent declarations that are in no sheet.
    """
    out: list[str] = []
    current: list[str] = []
    depth = 0
    quote = ""
    for char in body:
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == ";" and depth == 0:
            out.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    out.append("".join(current).strip())
    return [d for d in out if d]


def iter_rules(text: str, context: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], str, str]]:
    """Yield (at-rule context, prelude, body) for every block in a stylesheet."""
    index = 0
    length = len(text)
    while index < length:
        open_at = text.find("{", index)
        if open_at < 0:
            return
        prelude = text[index:open_at].strip()
        depth = 1
        cursor = open_at + 1
        while cursor < length and depth:
            if text[cursor] == "{":
                depth += 1
            elif text[cursor] == "}":
                depth -= 1
            cursor += 1
        body = text[open_at + 1:cursor - 1]
        if prelude.startswith(("@media", "@supports", "@layer", "@container")):
            yield from iter_rules(body, context + (" ".join(prelude.split()),))
        else:
            yield context, " ".join(prelude.split()), body
        index = cursor


def declaration_keys(text: str) -> set[tuple[tuple[str, ...], str, str]]:
    """The identity of everything a stylesheet declares.

    A style rule contributes one key per declaration, so a partial hoist
    regression is visible. An atomic at-rule contributes exactly one key for its
    whole body, because its declarations cannot be shared piecemeal.
    """
    keys: set[tuple[tuple[str, ...], str, str]] = set()
    for context, prelude, body in iter_rules(COMMENT_RE.sub("", text)):
        if not prelude:
            continue
        if prelude.startswith(ATOMIC_AT_RULES):
            keys.add((context, prelude, "; ".join(split_declarations(body))))
        else:
            for declaration in split_declarations(body):
                keys.add((context, prelude, declaration))
    return keys


def paper_of(provenance: dict[str, Any]) -> tuple[float, float] | None:
    match = PAPER_RE.match(str(provenance.get("paper", "")))
    return (float(match.group(1)), float(match.group(2))) if match else None


def near(left: float, right: float) -> bool:
    return abs(left - right) <= PT_TOLERANCE


# ---------------------------------------------------------------------------
# check 1 -- the files a bundle must and must not have
# ---------------------------------------------------------------------------


def check_bundle_files(tree: Tree) -> Result:
    result = Result("bundle-files", "")
    with_guide = 0
    for bundle in tree.bundles:
        for name in REQUIRED_BUNDLE_FILES:
            if not bundle.has(name):
                result.failures.append(f"{tree.rel(bundle.path / name)}: missing")

        has_html, has_css = (bundle.has(name) for name in GUIDE_FILES)
        if has_html != has_css:
            present, absent = GUIDE_FILES if has_html else GUIDE_FILES[::-1]
            result.failures.append(
                f"{tree.rel(bundle.path / present)}: present but "
                f"{absent} is missing -- a bundle has both or neither")
        with_guide += has_html and has_css

        # A bundle with no guide must not offer a link to one. A dead
        # "Guidelines and Instructions" link is worse than no link.
        if not has_html:
            for name in (*REQUIRED_BUNDLE_FILES, *GUIDE_FILES):
                if not name.endswith((".html", ".css")) or not bundle.has(name):
                    continue
                document = bundle.path / name
                for ref in local_references(document, read_text(document)):
                    if pathlib.PurePosixPath(ref.path).name in GUIDE_FILES:
                        result.failures.append(
                            f"{tree.rel(document)}: links {ref.raw!r} but this bundle "
                            f"has no guide")
    result.headline = (f"{len(tree.bundles)} bundles, {with_guide} with a guide, "
                       f"{len(tree.bundles) - with_guide} without")
    return result


# ---------------------------------------------------------------------------
# check 2 -- every local reference resolves
# ---------------------------------------------------------------------------


def check_local_refs(tree: Tree) -> Result:
    result = Result("local-refs", "")
    checked = 0
    encoded = 0
    pinned_present = 0
    pinned_absent: list[str] = []

    for bundle, document in tree.documents():
        pinned = {f"{GUIDE_PDF_DIR}/{name}" for name in (bundle.guide_pdfs if bundle else [])}
        for ref in local_references(document, read_text(document)):
            checked += 1
            encoded += ref.raw != ref.path
            try:
                inside = ref.target.relative_to(tree.root.resolve())
            except ValueError:
                result.failures.append(
                    f"{tree.rel(document)}: {ref.raw!r} escapes the tree "
                    f"({ref.target})")
                continue
            if ref.path in pinned:
                # The pinned official PDF is not tracked, by design. Its absence
                # is a fact about the repository, not a defect in the bundle;
                # its *presence* is an opportunity to verify the pin.
                if ref.target.is_file():
                    pinned_present += 1
                else:
                    pinned_absent.append(f"{tree.rel(document)} -> {inside}")
                continue
            if not ref.target.is_file():
                result.failures.append(
                    f"{tree.rel(document)}: {ref.raw!r} resolves to {inside}, "
                    f"which does not exist")

    result.headline = (f"{checked} local references, {encoded} percent-encoded, "
                       f"{pinned_present} pinned PDFs present")
    if pinned_absent:
        result.notes.append(
            f"{len(pinned_absent)} reference(s) point at a pinned source PDF that this "
            f"checkout does not carry (*.pdf is gitignored); existence NOT CHECKED: "
            + ", ".join(sorted(pinned_absent)))
    return result


# ---------------------------------------------------------------------------
# check 3 -- provenance is complete and agrees with the documents
# ---------------------------------------------------------------------------


def check_provenance(tree: Tree) -> Result:
    result = Result("provenance", "")
    verified_pins = 0
    unverifiable_pins: list[str] = []

    for bundle in tree.bundles:
        label = tree.rel(bundle.path / "provenance.json")
        if bundle.provenance is None:
            result.failures.append(f"{label}: {bundle.provenance_error}")
            continue
        record = bundle.provenance
        fail = lambda message: result.failures.append(f"{label}: {message}")  # noqa: E731

        for key, want in (("slug", str), ("code", str), ("revision", str),
                          ("source_file", str), ("sha256", str), ("generator", str),
                          ("pages", int), ("in_corpus", bool), ("uniform_paper", bool)):
            value = record.get(key)
            if not isinstance(value, want) or (want is str and not value.strip()):
                fail(f"{key} is {value!r}, expected a non-empty {want.__name__}")

        if record.get("slug") != bundle.slug:
            fail(f"slug {record.get('slug')!r} does not match the directory {bundle.slug!r}")
        if record.get("in_corpus") is bundle.in_extra:
            fail(f"in_corpus is {record.get('in_corpus')!r} for a bundle "
                 f"{'under' if bundle.in_extra else 'outside'} {EXTRA_DIR}/")
        if not HEX64_RE.match(str(record.get("sha256", ""))):
            fail(f"sha256 {record.get('sha256')!r} is not 64 lowercase hex digits")
        if isinstance(record.get("pages"), int) and record["pages"] < 1:
            fail(f"pages is {record['pages']}")

        # The generator has to name itself, so a hand-written bundle cannot pass
        # for a generated one.
        if "formgen" not in str(record.get("generator", "")):
            fail(f"generator {record.get('generator')!r} does not name tools/formgen")

        paper = paper_of(record)
        if paper is None:
            fail(f"paper {record.get('paper')!r} is not WIDTHxHEIGHT in points")

        _check_sources(record, bundle, fail)
        verified, unverified = _check_guide_record(record, bundle, tree, fail)
        verified_pins += verified
        unverifiable_pins += unverified
        if paper is not None:
            _check_geometry(record, bundle, tree, paper, fail)

    result.headline = (f"{len(tree.bundles)} records, {verified_pins} guide PDF "
                       f"hash(es) verified against the pin")
    if unverifiable_pins:
        result.notes.append(
            f"{len(unverifiable_pins)} recorded guide PDF(s) absent from this checkout, "
            f"so their sha256 pin was NOT CHECKED: " + ", ".join(sorted(unverifiable_pins)))
    return result


def _check_sources(record: dict[str, Any], bundle: Bundle, fail) -> None:
    sources = record.get("sources")
    if not isinstance(sources, list) or not sources:
        fail(f"sources is {sources!r}, expected a non-empty list")
        return
    for index, entry in enumerate(sources):
        if not isinstance(entry, dict):
            fail(f"sources[{index}] is not an object")
            continue
        for key in ("role", "file", "sha256"):
            if not str(entry.get(key, "")).strip():
                fail(f"sources[{index}].{key} is empty")
        if not HEX64_RE.match(str(entry.get("sha256", ""))):
            fail(f"sources[{index}].sha256 {entry.get('sha256')!r} is not 64 hex digits")
    forms = [e for e in sources if isinstance(e, dict) and e.get("role") == "form"]
    if len(forms) != 1:
        fail(f"{len(forms)} sources with role 'form', expected exactly 1")
        return
    if forms[0].get("file") != record.get("source_file"):
        fail(f"sources form file {forms[0].get('file')!r} != source_file "
             f"{record.get('source_file')!r}")
    if forms[0].get("sha256") != record.get("sha256"):
        fail("sources form sha256 disagrees with the record's sha256")


def _check_guide_record(record: dict[str, Any], bundle: Bundle, tree: Tree,
                        fail) -> tuple[int, list[str]]:
    """The guide half of provenance, including the pinned PDFs it claims."""
    guide = record.get("guide")
    has_guide_html = bundle.has("guide.html")
    if guide is None:
        if has_guide_html:
            fail("guide is null but guide.html exists")
        return 0, []
    if not isinstance(guide, dict):
        fail(f"guide is a {type(guide).__name__}, not an object or null")
        return 0, []
    if not has_guide_html:
        fail("guide is recorded but guide.html does not exist")
    if guide.get("document") != "guide.html":
        fail(f"guide.document is {guide.get('document')!r}, expected 'guide.html'")

    pdf_sources = {e.get("file") for e in record.get("sources", [])
                   if isinstance(e, dict) and e.get("role") == "guide"}
    entries = guide.get("standalone_pdfs")
    if not isinstance(entries, list):
        fail(f"guide.standalone_pdfs is {entries!r}, expected a list")
        return 0, []

    verified = 0
    unverifiable: list[str] = []
    linked: set[str] = set()
    if has_guide_html:
        document = bundle.path / "guide.html"
        linked = {ref.path for ref in local_references(document, read_text(document))}

    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            fail(f"guide.standalone_pdfs[{index}] is not an object")
            continue
        name = str(entry.get("file", ""))
        expected_link = f"{GUIDE_PDF_DIR}/{name}"
        if not HEX64_RE.match(str(entry.get("sha256", ""))):
            fail(f"guide.standalone_pdfs[{index}].sha256 is not 64 hex digits")
        if entry.get("linked_as") != expected_link:
            fail(f"guide.standalone_pdfs[{index}].linked_as "
                 f"{entry.get('linked_as')!r} != {expected_link!r}")
        if name not in pdf_sources:
            fail(f"guide PDF {name!r} has no sources entry with role 'guide'")
        if expected_link not in linked:
            fail(f"guide.html does not link the recorded guide PDF {expected_link!r}")
        on_disk = bundle.path / GUIDE_PDF_DIR / name
        if on_disk.is_file():
            digest = hashlib.sha256(on_disk.read_bytes()).hexdigest()
            if digest != entry.get("sha256"):
                fail(f"{GUIDE_PDF_DIR}/{name} hashes to {digest[:16]}…, "
                     f"pinned as {str(entry.get('sha256'))[:16]}…")
            else:
                verified += 1
        else:
            unverifiable.append(f"{bundle.rel}/{GUIDE_PDF_DIR}/{name}")

    for name in sorted(pdf_sources - {str(e.get("file")) for e in entries
                                      if isinstance(e, dict)}):
        fail(f"sources records guide PDF {name!r} that guide.standalone_pdfs omits")
    return verified, unverifiable


def _check_geometry(record: dict[str, Any], bundle: Bundle, tree: Tree,
                    paper: tuple[float, float], fail) -> None:
    """The recorded paper must be the paper the documents actually set."""
    width, height = paper
    for name in ("form.css", "guide.css"):
        if not bundle.has(name):
            continue
        match = AT_PAGE_RE.search(COMMENT_RE.sub("", read_text(bundle.path / name)))
        if match is None:
            fail(f"{name} has no @page size")
            continue
        if not (near(float(match.group(1)), width) and near(float(match.group(2)), height)):
            fail(f"{name} @page is {match.group(1)}pt {match.group(2)}pt, "
                 f"paper is {record.get('paper')}")

    if not bundle.has("index.html"):
        return
    html = read_text(bundle.path / "index.html")
    # Each page is checked against ITS OWN recorded size, not the bundle's.
    # 1604-CF's page 3 really is landscape (1008x612 among three 612x1008) and
    # the emission is faithful; comparing every page to the single bundle paper
    # reported that faithful page as a defect. The bundle paper stays the
    # fallback for records written before page_papers existed, so an older
    # bundle still gets checked rather than silently skipped.
    page_papers = record.get("page_papers")
    if not isinstance(page_papers, list):
        page_papers = []
    ids: list[str] = []
    for index, tag in enumerate(PAGE_DIV_RE.findall(html)):
        attrs = dict(ATTR_RE.findall(tag))
        ids.append(attrs.get("id", ""))
        style = attrs.get("style", "")
        got = {axis: LENGTH_RE[axis].search(style) for axis in LENGTH_RE}
        if not all(got.values()):
            fail(f"index.html page {attrs.get('id')!r} has no pt width/height")
            continue
        want = paper_of({"paper": page_papers[index]}) if index < len(page_papers) else None
        if want is None:
            want = (width, height)
            source = f"paper is {record.get('paper')}"
        else:
            source = f"page_papers[{index}] is {page_papers[index]}"
        if not (near(float(got["width"].group(1)), want[0])
                and near(float(got["height"].group(1)), want[1])):
            fail(f"index.html page {attrs.get('id')!r} is "
                 f"{got['width'].group(1)}x{got['height'].group(1)}pt, "
                 f"{source}")

    if ids != [f"page-{n}" for n in range(1, len(ids) + 1)]:
        fail(f"index.html page ids are {ids}, expected page-1..page-{len(ids)}")

    # A page can be absent from the form only because the guide took all of it:
    # guides.py records that as a cut at y=0.
    guide = record.get("guide") or {}
    whole_page_cuts = sum(1 for cut in guide.get("moved_from_form") or []
                          if isinstance(cut, dict) and float(cut.get("cut_y_pt", -1)) == 0.0)
    pages = record.get("pages")
    if isinstance(pages, int) and len(ids) != pages - whole_page_cuts:
        fail(f"index.html carries {len(ids)} page(s); provenance says {pages} source "
             f"page(s) with {whole_page_cuts} moved wholly to the guide")


# ---------------------------------------------------------------------------
# check 4 -- the CSS split still holds
# ---------------------------------------------------------------------------


def check_css_split(tree: Tree) -> Result:
    result = Result("css-split", "")
    base_path = tree.root / "base.css"
    if not base_path.is_file():
        result.failures.append(f"{tree.rel(base_path)}: missing -- every bundle links it")
        result.headline = "no base.css"
        return result

    base_keys = declaration_keys(read_text(base_path))
    duplicates = 0
    for bundle in tree.bundles:
        for name in ("form.css", "guide.css"):
            if not bundle.has(name):
                continue
            for context, prelude, declaration in sorted(
                    declaration_keys(read_text(bundle.path / name)) & base_keys):
                duplicates += 1
                where = " ".join((*context, prelude))
                result.failures.append(
                    f"{tree.rel(bundle.path / name)}: {where} {{ {declaration} }} is "
                    f"byte-identical to base.css -- the hoist regressed")

        # The link, and its depth. A bundle under extra/ needs one more climb.
        for name in ("index.html", "guide.html"):
            if not bundle.has(name):
                continue
            document = bundle.path / name
            refs = [r for r in local_references(document, read_text(document))
                    if pathlib.PurePosixPath(r.path).name == "base.css"]
            if not refs:
                result.failures.append(f"{tree.rel(document)}: does not link base.css")
                continue
            for ref in refs:
                if ref.path != f"{bundle.depth}base.css":
                    result.failures.append(
                        f"{tree.rel(document)}: links base.css as {ref.raw!r}, "
                        f"expected {bundle.depth}base.css")
            own = "form.css" if name == "index.html" else "guide.css"
            own_refs = [r for r in local_references(document, read_text(document))
                        if r.path == own]
            if not own_refs:
                result.failures.append(f"{tree.rel(document)}: does not link {own}")

    result.headline = (f"{len(base_keys)} shared declarations in base.css, "
                       f"{duplicates} repeated in a bundle")
    return result


# ---------------------------------------------------------------------------
# check 5 -- the shared pool
# ---------------------------------------------------------------------------


def _load_asset_manifest(tree: "Tree", result: "Result") -> dict[str, str] | None:
    """filename -> the sha256 of that file's own bytes, or None if absent.

    Absent is a failure, not a fallback: without it the soft-masked assets go
    back to being unverifiable, and reporting them as merely "not checked" is how
    27% of the pool sat unexamined. None is returned only so the caller can name
    each one, having already recorded the missing manifest.
    """
    path = tree.root / ASSET_MANIFEST
    if not path.is_file():
        result.failures.append(
            f"{ASSET_MANIFEST} is missing, so no soft-masked asset can be verified")
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        entries = payload["assets"]
        return {name: entry["sha256"] for name, entry in entries.items()}
    except (ValueError, KeyError, TypeError) as exc:
        result.failures.append(f"{ASSET_MANIFEST} is unreadable: {exc}")
        return None


def check_shared_pool(tree: Tree) -> Result:
    result = Result("shared-pool", "")
    for name in SHARED_DIRS:
        if not (tree.root / name).is_dir():
            result.failures.append(f"{tree.rel(tree.root / name)}/: missing")
    if result.failures:
        result.headline = "the shared pool is not there"
        return result

    assets = sorted(p for p in (tree.root / ASSET_DIR).iterdir() if p.is_file())
    fonts = sorted(p for p in (tree.root / FONT_DIR).iterdir() if p.is_file())

    # The composite digests batch.py records. An asset is named for the sha256 of
    # the base image stream in its source PDF; when that source declares a soft
    # mask the file on disk is the composite painted over white, so the name
    # cannot verify the bytes and recomputing them needs a PDF this check does
    # not have. The manifest carries the file's own digest, which is the
    # independent fact that closes the gap: 32 of 119 assets used to be
    # unverifiable, i.e. indistinguishable from corrupted.
    manifest = _load_asset_manifest(tree, result)

    content_verified = 0
    manifest_verified = 0
    unverifiable: list[str] = []
    for asset in assets:
        match = ASSET_NAME_RE.match(asset.name)
        if match is None:
            result.failures.append(
                f"{tree.rel(asset)}: not named <64 hex>.<ext>; content-hash naming is "
                f"what makes one pool safe to share between bundles")
            continue
        payload = asset.read_bytes()
        _check_magic(asset, match.group(2), payload, tree, result)
        digest = hashlib.sha256(payload).hexdigest()
        if digest == match.group(1):
            content_verified += 1
        elif manifest is None:
            unverifiable.append(tree.rel(asset))
        elif asset.name not in manifest:
            result.failures.append(
                f"{tree.rel(asset)}: bytes do not hash to its name and "
                f"{ASSET_MANIFEST} does not record it, so nothing can tell it from "
                f"a corrupted file")
        elif manifest[asset.name] != digest:
            result.failures.append(
                f"{tree.rel(asset)}: hashes to {digest[:12]} but {ASSET_MANIFEST} "
                f"records {manifest[asset.name][:12]}")
        else:
            manifest_verified += 1

    if manifest is not None:
        for name in sorted(set(manifest) - {asset.name for asset in assets}):
            result.failures.append(
                f"{ASSET_MANIFEST} records {name}, which is not in the pool")

    for font in fonts:
        _check_magic(font, font.suffix.lstrip("."), font.read_bytes(), tree, result)

    referenced: set[str] = set()
    for bundle, document in tree.documents():
        for ref in local_references(document, read_text(document)):
            head = pathlib.PurePosixPath(ref.path).parts
            if not head or head[-2:-1] not in (("assets",), ("fonts",)):
                continue
            referenced.add(pathlib.PurePosixPath(ref.path).name)
            if bundle is None:
                continue
            # It has to climb out. A bundle-local assets/ or fonts/ copy defeats
            # sharing even when it resolves.
            if not ref.path.startswith(bundle.depth) or ".." in ref.path[len(bundle.depth):]:
                result.failures.append(
                    f"{tree.rel(document)}: references the shared pool as {ref.raw!r}, "
                    f"which does not climb out of the bundle "
                    f"(expected {bundle.depth}{head[-2]}/…)")

    for path in assets + fonts:
        if path.name not in referenced:
            result.failures.append(
                f"{tree.rel(path)}: in the shared pool but no document references it")

    sealed = _check_artwork_seals(tree, result)

    result.headline = (f"{len(assets)} assets ({content_verified} name-verified, "
                       f"{manifest_verified} manifest-verified, "
                       f"{sealed} seal-matched), {len(fonts)} fonts, "
                       f"{len(referenced)} referenced")
    if unverifiable:
        result.notes.append(
            f"{len(unverifiable)} asset(s) could be verified against neither their name "
            f"nor {ASSET_MANIFEST}, so their bytes were NOT CHECKED: "
            + ", ".join(sorted(unverifiable)))
    return result


def _check_artwork_seals(tree: Tree, result: Result) -> int:
    """`data-sha256` beside every artwork reference, and the two must agree.

    This is the one binding on an asset's identity that lives entirely inside
    the tree, so it is the only thing that catches a renamed asset in the 32
    cases whose bytes cannot be re-hashed to their name without the source PDF.
    """
    sealed = 0
    for bundle, document in tree.documents():
        if bundle is None or document.suffix != ".html":
            continue
        # Script bodies are not markup. The overlay's own source contains the
        # literal "<image>" inside a comment explaining that an image's bounding
        # box is not a wall, and a raw scan read that as an artwork tag with no
        # href and no seal -- 53 failures, one per bundle, for a defect that did
        # not exist. Strip <script>...</script> before scanning.
        # Note the limit this accepts: a reference CONSTRUCTED in script is now
        # invisible to this check. That is the correct trade here because the
        # emitted documents never build asset URLs at runtime -- asserted
        # directly below -- but if that ever changes, this check goes blind and
        # the assertion is what will say so.
        markup = SCRIPT_BODY_RE.sub("", read_text(document))
        if ASSET_URL_IN_SCRIPT_RE.search(read_text(document)):
            result.failures.append(
                f"{tree.rel(document)}: a script builds an asset URL, which the "
                f"markup scan cannot see -- teach this check to follow it")
        for tag in ARTWORK_TAG_RE.findall(markup):
            attrs = dict(ATTR_RE.findall(tag))
            seal = attrs.get("data-sha256")
            missing = attrs.get("data-missing-src")
            if missing is not None:
                result.failures.append(
                    f"{tree.rel(document)}: carries a placeholder for artwork it does "
                    f"not ship ({missing})")
                continue
            reference = attrs.get("href") or attrs.get("src") or ""
            if seal is None:
                result.failures.append(
                    f"{tree.rel(document)}: references {reference!r} with no "
                    f"data-sha256 seal")
                continue
            if not reference:
                result.failures.append(
                    f"{tree.rel(document)}: data-sha256 {seal[:16]}… on an element "
                    f"that loads nothing")
                continue
            name = pathlib.PurePosixPath(urllib.parse.unquote(reference)).name
            if name.split(".")[0] != seal:
                result.failures.append(
                    f"{tree.rel(document)}: loads {name} but seals it as {seal[:16]}…")
            else:
                sealed += 1
    return sealed


def _check_magic(path: pathlib.Path, extension: str, payload: bytes, tree: Tree,
                 result: Result) -> None:
    """A truncated or mislabelled binary is catchable without any source."""
    magic = FILE_MAGIC.get(extension.lower())
    if magic is None:
        result.failures.append(f"{tree.rel(path)}: unexpected file type {extension!r}")
        return
    head, tail = magic
    if not payload.startswith(head):
        result.failures.append(
            f"{tree.rel(path)}: does not start with the {extension} signature")
    elif tail is not None and not payload.endswith(tail):
        result.failures.append(f"{tree.rel(path)}: truncated -- no {extension} terminator")


# ---------------------------------------------------------------------------
# check 6 -- the findings ledger
# ---------------------------------------------------------------------------


def check_review_findings(tree: Tree) -> Result:
    result = Result("review-findings", "")
    label = tree.rel(tree.findings_path)
    if not tree.findings_path.is_file():
        result.failures.append(f"{label}: missing")
        result.headline = "no ledger"
        return result
    try:
        ledger = json.loads(tree.findings_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        result.failures.append(f"{label}: does not parse: {exc}")
        result.headline = "unparsable ledger"
        return result

    if not isinstance(ledger, dict):
        result.failures.append(f"{label}: top level is a {type(ledger).__name__}, "
                               f"expected an object")
        result.headline = "wrong shape"
        return result
    for key, want in (("schema_version", int), ("source", str), ("findings", list)):
        if not isinstance(ledger.get(key), want):
            result.failures.append(
                f"{label}: {key} is {ledger.get(key)!r}, expected {want.__name__}")
    findings = ledger.get("findings")
    if not isinstance(findings, list):
        result.headline = "no findings list"
        return result

    seen: set[str] = set()
    counts: dict[str, int] = {}
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            result.failures.append(f"{label}: findings[{index}] is not an object")
            continue
        ident = str(finding.get("id", f"#{index}"))
        for key in FINDING_KEYS:
            if key not in finding:
                result.failures.append(f"{label}: {ident} has no {key!r}")
        if ident in seen:
            result.failures.append(f"{label}: duplicate finding id {ident!r}")
        seen.add(ident)
        severity, status = finding.get("severity"), finding.get("status")
        if severity not in SEVERITIES:
            result.failures.append(
                f"{label}: {ident} severity {severity!r} not in {sorted(SEVERITIES)}")
        if status not in STATUSES:
            result.failures.append(
                f"{label}: {ident} status {status!r} not in {sorted(STATUSES)}")
        # A finding cannot be closed by assertion alone.
        if status in RESOLVED_STATUSES and not str(finding.get("resolution", "")).strip():
            result.failures.append(
                f"{label}: {ident} is {status} with an empty resolution")
        key = f"{severity}/{status}"
        counts[key] = counts.get(key, 0) + 1

    result.headline = (f"{len(findings)} findings; "
                       + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    return result


# ---------------------------------------------------------------------------
# check 7 -- nothing the generator did not write
# ---------------------------------------------------------------------------


def check_strays(tree: Tree) -> Result:
    result = Result("no-strays", "")
    allowed_files = set(REQUIRED_BUNDLE_FILES) | set(GUIDE_FILES)
    counted = 0
    for bundle in tree.bundles:
        recorded = set(bundle.guide_pdfs)
        for entry in sorted(bundle.path.iterdir()):
            counted += 1
            if entry.is_dir():
                if entry.name != GUIDE_PDF_DIR:
                    result.failures.append(
                        f"{tree.rel(entry)}/: not a directory batch.py writes")
                    continue
                if not recorded:
                    result.failures.append(
                        f"{tree.rel(entry)}/: present but provenance records no guide PDF")
                for pdf in sorted(entry.iterdir()):
                    if pdf.name not in recorded:
                        result.failures.append(
                            f"{tree.rel(pdf)}: not recorded in "
                            f"guide.standalone_pdfs")
                continue
            if entry.name not in allowed_files:
                result.failures.append(f"{tree.rel(entry)}: not a file batch.py writes")

    for entry in sorted(tree.root.iterdir()):
        if entry.name not in ROOT_ENTRIES and entry not in [b.path for b in tree.bundles]:
            result.failures.append(f"{tree.rel(entry)}: not part of the tree layout")
    extra = tree.root / EXTRA_DIR
    if extra.is_dir():
        for entry in sorted(extra.iterdir()):
            if not entry.is_dir():
                result.failures.append(f"{tree.rel(entry)}: {EXTRA_DIR}/ holds bundles only")

    result.headline = f"{counted} entries across {len(tree.bundles)} bundles"
    return result


CHECKS = (
    ("1 bundle files", check_bundle_files),
    ("2 local refs", check_local_refs),
    ("3 provenance", check_provenance),
    ("4 css split", check_css_split),
    ("5 shared pool", check_shared_pool),
    ("6 review findings", check_review_findings),
    ("7 no strays", check_strays),
)


def run_checks(root: pathlib.Path, findings: pathlib.Path) -> list[Result]:
    tree = load_tree(root, findings)
    return [check(tree) for _, check in CHECKS]


def report(results: list[Result], verbose: bool, elapsed: float,
           stream=sys.stdout) -> int:
    limit = 10 ** 9 if verbose else 12
    for (title, _), result in zip(CHECKS, results):
        mark = "PASS" if result.ok else "FAIL"
        # The headline prints either way: a failing check's scale is as much of
        # the answer as its first twelve lines.
        detail = (result.headline if result.ok
                  else f"{len(result.failures)} failure(s); {result.headline}")
        print(f"{mark}  {title:<18} {detail}", file=stream)
        for line in result.failures[:limit]:
            print(f"        {line}", file=stream)
        if len(result.failures) > limit:
            print(f"        … and {len(result.failures) - limit} more "
                  f"(--verbose for all)", file=stream)
        for note in result.notes:
            print(f"  NOT-RUN {note}", file=stream)

    failed = [r for r in results if not r.ok]
    not_run = sum(len(r.notes) for r in results)
    print(f"\n{len(results) - len(failed)}/{len(results)} checks pass, "
          f"{sum(len(r.failures) for r in failed)} failure(s), "
          f"{not_run} component(s) not evaluable here (never counted as a pass) "
          f"in {elapsed:.2f}s", file=stream)
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# self-test: a validator that cannot fail is worthless
# ---------------------------------------------------------------------------


def _png(payload: bytes) -> bytes:
    """Enough of a PNG for the magic check; the bytes themselves are the point."""
    return b"\x89PNG\r\n\x1a\n" + payload + b"IEND\xaeB`\x82"


BASE_CSS = """\
/* shared */
* { margin:0;padding:0;box-sizing:border-box }
.page { position:relative;overflow:hidden;background:#fff }
@font-face { font-family:"F";font-display:block;src:url("fonts/shared face.woff2") format("woff2") }
@media print { .fi{border:0} }
"""

FORM_CSS = """\
/* {slug} -- form-specific.
 * Shared scaffolding is in {depth}base.css. */
@page {{ size:612pt 936pt;margin:0 }}
.fi {{ position:absolute;inset:0;color:#000 }}
@font-face {{ font-family:"G";font-display:block;src:url("{depth}fonts/shared face.woff2") format("woff2") }}
"""

GUIDE_CSS = """\
/* {slug} -- guide. */
@page {{ size:612pt 936pt;margin:0 }}
.gl {{ margin:0 auto;max-width:46em }}
"""

INDEX_HTML = """\
<!doctype html>
<html lang="en" data-form="{code}">
<head>
<meta charset="utf-8">
<title>BIR Form {code}</title>
<link rel="stylesheet" href="{depth}base.css">
<link rel="stylesheet" href="form.css">
</head>
<body>
{guide_link}<div class="page page-1" id="page-1" style="width:612pt;height:936pt">
<img class="img" src="{depth}assets/{asset}" alt="" data-sha256="{seal}">
<img class="img" src="{depth}assets/{masked}" alt="" data-sha256="{masked_seal}">
<span class="t">{code}</span>
</div>
</body>
</html>
"""

GUIDE_HTML = """\
<!doctype html>
<html lang="en" data-form="{code}" data-document="guide">
<head>
<meta charset="utf-8">
<title>BIR Form {code} -- Guidelines</title>
<link rel="stylesheet" href="{depth}base.css">
<link rel="stylesheet" href="guide.css">
</head>
<body>
<div class="gl"><a href="index.html">Back</a>{pdf_link}</div>
</body>
</html>
"""


def _provenance(slug: str, code: str, in_corpus: bool, guide_pdf: tuple[str, str] | None,
                has_guide: bool) -> dict[str, Any]:
    sources = [{"role": "form", "file": f"{code}.pdf", "sha256": "a" * 64}]
    guide: dict[str, Any] | None = None
    if has_guide:
        guide = {"document": "guide.html", "origins": [], "moved_from_form": [],
                 "standalone_pdfs": []}
    if guide_pdf is not None:
        name, digest = guide_pdf
        sources.append({"role": "guide", "file": name, "sha256": digest})
        guide = guide or {"document": "guide.html", "origins": [], "moved_from_form": []}
        guide["standalone_pdfs"] = [{"file": name, "sha256": digest,
                                     "linked_as": f"{GUIDE_PDF_DIR}/{name}",
                                     "reflowed_into": "guide.html"}]
    return {"slug": slug, "code": code, "revision": "2018", "variant": "",
            "in_corpus": in_corpus, "source_file": f"{code}.pdf", "sha256": "a" * 64,
            "pages": 1, "paper": "612.0x936.0", "uniform_paper": True,
            "sources": sources, "guide": guide,
            "generator": "tools/formgen batch.py (rule-backend=svg)"}


FIXTURE_FINDING = {
    "id": "F001", "form": "alpha-2018", "page": 1, "severity": "major",
    "status": "fixed", "what": "a rule was 0.24pt where the source draws 0.48pt",
    "where": "page 1 at (22.3, 50.2)pt", "evidence": "IR vs emitted SVG",
    "resolution": "emit.py now rounds to the source width; verify.py clean",
    "cause": "c1", "audit_blind": True,
}


def build_fixture(root: pathlib.Path) -> pathlib.Path:
    """A minimal tree with the shape of the real one, and no defects."""
    forms = root / "forms"
    (forms / FONT_DIR).mkdir(parents=True)
    (forms / ASSET_DIR).mkdir(parents=True)
    (forms / "base.css").write_text(BASE_CSS, encoding="utf-8")
    # The space is deliberate: it is what forced percent-decoding.
    (forms / FONT_DIR / "shared face.woff2").write_bytes(b"wOF2" + b"\0" * 32)
    asset_bytes = _png(b"fixture")
    asset = f"{hashlib.sha256(asset_bytes).hexdigest()}.png"
    (forms / ASSET_DIR / asset).write_bytes(asset_bytes)

    # A soft-masked asset: named for a base stream it no longer contains, so its
    # name cannot verify it and only the manifest can. The fixture carries one
    # because it is the case that used to be unverifiable, and a fixture without
    # it would let that regress unnoticed.
    masked_bytes = _png(b"fixture composite")
    masked = f"{'e' * 64}.png"
    (forms / ASSET_DIR / masked).write_bytes(masked_bytes)
    (forms / ASSET_MANIFEST).write_text(json.dumps({
        "schema_version": 1,
        "assets": {
            asset: {"sha256": hashlib.sha256(asset_bytes).hexdigest(),
                    "sources": ["alpha-2018"]},
            masked: {"sha256": hashlib.sha256(masked_bytes).hexdigest(),
                     "sources": ["alpha-2018"]},
        }}, indent=2) + "\n", encoding="utf-8")

    guide_pdf = b"%PDF-1.4 fixture guide"
    guide_name = "Alpha Guide 2018.pdf"
    guide_digest = hashlib.sha256(guide_pdf).hexdigest()

    for slug, code, in_corpus, has_guide, with_pdf in (
            ("alpha-2018", "ALPHA", True, False, False),
            ("beta-2019", "BETA", False, True, True)):
        path = forms / (slug if in_corpus else f"{EXTRA_DIR}/{slug}")
        path.mkdir(parents=True)
        depth = "../" if in_corpus else "../../"
        (path / "form.css").write_text(FORM_CSS.format(slug=slug, depth=depth),
                                       encoding="utf-8")
        guide_link = ('<a class="doc-link" href="guide.html">Guidelines</a>\n'
                      if has_guide else "")
        (path / "index.html").write_text(
            INDEX_HTML.format(code=code, depth=depth, asset=asset, guide_link=guide_link,
                              masked=masked,
                              masked_seal=masked.split('.')[0],
                              seal=asset.split(".")[0]),
            encoding="utf-8")
        if has_guide:
            (path / "guide.css").write_text(GUIDE_CSS.format(slug=slug), encoding="utf-8")
            quoted = urllib.parse.quote(guide_name)
            pdf_link = (f'<a href="{GUIDE_PDF_DIR}/{quoted}">PDF</a>' if with_pdf else "")
            (path / "guide.html").write_text(
                GUIDE_HTML.format(code=code, depth=depth, pdf_link=pdf_link),
                encoding="utf-8")
        if with_pdf:
            (path / GUIDE_PDF_DIR).mkdir()
            (path / GUIDE_PDF_DIR / guide_name).write_bytes(guide_pdf)
        record = _provenance(slug, code, in_corpus,
                             (guide_name, guide_digest) if with_pdf else None, has_guide)
        (path / "provenance.json").write_text(json.dumps(record, indent=2) + "\n",
                                              encoding="utf-8")

    (root / "review-findings.json").write_text(
        json.dumps({"schema_version": 1, "source": "fixture", "cause_codes": {"c1": "x"},
                    "findings": [FIXTURE_FINDING]}, indent=2) + "\n", encoding="utf-8")
    return forms


# Each mutation names the check it must trip. `alpha` has no guide, `beta` is
# under extra/ and carries a pinned guide PDF.
ALPHA = "forms/alpha-2018"
BETA = "forms/extra/beta-2019"


def _edit(root: pathlib.Path, rel: str, old: str, new: str) -> None:
    path = root / rel
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise AssertionError(f"fixture no longer contains {old!r} in {rel}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _patch_json(root: pathlib.Path, rel: str, mutate) -> None:
    path = root / rel
    record = json.loads(path.read_text(encoding="utf-8"))
    mutate(record)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def _rename_asset(root: pathlib.Path, reseal: bool) -> str:
    """Rename the pooled asset and repoint every reference. Returns the new name.

    With `reseal` the markup's data-sha256 moves too, which is the case no
    tree-only check can catch: the bytes no longer hash to the name, and neither
    do 32 legitimate soft-masked assets in the real pool.
    """
    pool = root / "forms" / ASSET_DIR
    asset = next(iter(sorted(pool.iterdir())))
    wrong = pool / f"{'b' * 64}.png"
    asset.rename(wrong)
    for rel in (f"{ALPHA}/index.html", f"{BETA}/index.html"):
        _edit(root, rel, f"assets/{asset.name}", f"assets/{wrong.name}")
        if reseal:
            _edit(root, rel, f'data-sha256="{asset.name.split(".")[0]}"',
                  f'data-sha256="{"b" * 64}"')
    return wrong.name


def _bundle_local_asset(root: pathlib.Path) -> None:
    """A bundle-local copy of a shared asset: it resolves, and it defeats sharing."""
    pool = root / "forms" / ASSET_DIR
    asset = next(iter(sorted(pool.iterdir())))
    local = root / ALPHA / ASSET_DIR
    local.mkdir()
    shutil.copy(asset, local / asset.name)
    _edit(root, f"{ALPHA}/index.html", f"../{ASSET_DIR}/", f"{ASSET_DIR}/")


MUTATIONS: tuple[tuple[str, Any, str], ...] = (
    ("a bundle with no form.css",
     lambda r: (r / ALPHA / "form.css").unlink(), "1 bundle files"),
    ("a guide.html with no guide.css",
     lambda r: (r / BETA / "guide.css").unlink(), "1 bundle files"),
    ("a bundle with no guide linking one",
     lambda r: _edit(r, f"{ALPHA}/index.html", "<body>",
                     '<body>\n<a href="guide.html">Guidelines</a>'), "1 bundle files"),
    ("a dangling href",
     lambda r: _edit(r, f"{ALPHA}/index.html", "</body>",
                     '<a href="missing-sheet.html">x</a></body>'), "2 local refs"),
    ("a dangling percent-encoded href",
     lambda r: _edit(r, f"{BETA}/guide.html", "Alpha%20Guide%202018.pdf",
                     f"{GUIDE_PDF_DIR}/Gone%20Guide.pdf"), "2 local refs"),
    ("a reference that escapes the tree",
     lambda r: _edit(r, f"{ALPHA}/index.html", "../assets/", "../../../assets/"),
     "2 local refs"),
    ("a truncated sha256",
     lambda r: _patch_json(r, f"{ALPHA}/provenance.json",
                           lambda d: d.update(sha256="a" * 63)), "3 provenance"),
    ("a slug that is not the directory",
     lambda r: _patch_json(r, f"{ALPHA}/provenance.json",
                           lambda d: d.update(slug="gamma-2018")), "3 provenance"),
    ("paper that disagrees with @page",
     lambda r: _patch_json(r, f"{ALPHA}/provenance.json",
                           lambda d: d.update(paper="612.0x1008.0")), "3 provenance"),
    ("a page count the guide does not explain",
     lambda r: _patch_json(r, f"{ALPHA}/provenance.json",
                           lambda d: d.update(pages=3)), "3 provenance"),
    ("an unrecorded guide PDF hash",
     lambda r: _patch_json(r, f"{BETA}/provenance.json",
                           lambda d: d["guide"]["standalone_pdfs"][0].update(sha256="c" * 64)),
     "3 provenance"),
    ("a guide PDF missing from provenance",
     lambda r: _patch_json(r, f"{BETA}/provenance.json",
                           lambda d: d["guide"].update(standalone_pdfs=[])),
     "3 provenance"),
    ("a generator that does not name itself",
     lambda r: _patch_json(r, f"{ALPHA}/provenance.json",
                           lambda d: d.update(generator="hand-written")), "3 provenance"),
    ("a declaration hoisted back out of base.css",
     lambda r: _edit(r, f"{ALPHA}/form.css", ".fi { position:absolute",
                     ".page { position:relative;overflow:hidden;background:#fff }\n"
                     ".fi { position:absolute"), "4 css split"),
    ("base.css linked at the wrong depth",
     lambda r: _edit(r, f"{BETA}/index.html", "../../base.css", "../base.css"),
     "4 css split"),
    ("a renamed asset the markup still seals",
     lambda r: _rename_asset(r, reseal=False), "5 shared pool"),
    ("artwork the bundle does not ship",
     lambda r: _edit(r, f"{ALPHA}/index.html", "<img class=\"img\"",
                     "<div data-missing-src=\"../assets/gone.png\" class=\"img\"></div><img class=\"img\""),
     "5 shared pool"),
    ("an artwork reference with no seal",
     lambda r: _edit(r, f"{ALPHA}/index.html", ' data-sha256="', ' data-unsealed="'),
     "5 shared pool"),
    ("a bundle-local copy of a shared asset",
     _bundle_local_asset, "5 shared pool"),
    ("an asset nothing references",
     lambda r: (r / "forms" / ASSET_DIR / f"{'d' * 64}.png").write_bytes(_png(b"orphan")),
     "5 shared pool"),
    ("a truncated asset",
     lambda r: _truncate_asset(r), "5 shared pool"),
    ("a severity outside the vocabulary",
     lambda r: _patch_json(r, "review-findings.json",
                           lambda d: d["findings"][0].update(severity="critical")),
     "6 review findings"),
    ("a status outside the vocabulary",
     lambda r: _patch_json(r, "review-findings.json",
                           lambda d: d["findings"][0].update(status="done")),
     "6 review findings"),
    ("a fixed finding with no resolution",
     lambda r: _patch_json(r, "review-findings.json",
                           lambda d: d["findings"][0].update(resolution="  ")),
     "6 review findings"),
    ("a ledger that does not parse",
     lambda r: (r / "review-findings.json").write_text("{not json", encoding="utf-8"),
     "6 review findings"),
    ("a stray file in a bundle",
     lambda r: (r / ALPHA / "form.css.orig").write_text("x", encoding="utf-8"),
     "7 no strays"),
    ("a leftover directory in a bundle",
     lambda r: (r / ALPHA / "old-slug").mkdir(), "7 no strays"),
    ("an unrecorded PDF in guides/",
     lambda r: (r / BETA / GUIDE_PDF_DIR / "Stray.pdf").write_bytes(b"%PDF-1.4 x"),
     "7 no strays"),
)


def _truncate_asset(root: pathlib.Path) -> None:
    pool = root / "forms" / ASSET_DIR
    asset = next(iter(sorted(pool.iterdir())))
    asset.write_bytes(asset.read_bytes()[:-8])


def self_test() -> int:
    """Prove the pristine fixture passes and that every check can be made to fail."""
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        pristine = pathlib.Path(tmp) / "pristine"
        pristine.mkdir()
        build_fixture(pristine)
        results = run_checks(pristine / "forms", pristine / "review-findings.json")
        clean = [r for r in results if not r.ok]
        if clean:
            failures += 1
            print("  FAIL  the pristine fixture is not clean:")
            for result in clean:
                for line in result.failures:
                    print(f"          {result.name}: {line}")
        else:
            print("  PASS  the pristine fixture passes all 7 checks")

        # The percent-decoding regression, stated as its own case: the fixture
        # links "Alpha%20Guide%202018.pdf" and the file on disk has spaces.
        pinned = [r for r in results if r.name == "provenance"][0]
        ok = "1 guide PDF hash(es) verified" in pinned.headline
        failures += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  a percent-encoded name resolves and its "
              f"pin is verified{'' if ok else f': {pinned.headline}'}")

        # A pinned PDF that is absent -- the CI case -- is a NOT-RUN, not a pass
        # and not a failure.
        absent = pathlib.Path(tmp) / "absent"
        shutil.copytree(pristine, absent)
        (absent / BETA / GUIDE_PDF_DIR / "Alpha Guide 2018.pdf").unlink()
        results = run_checks(absent / "forms", absent / "review-findings.json")
        by_name = {r.name: r for r in results}
        ok = (by_name["local-refs"].ok and by_name["provenance"].ok
              and by_name["local-refs"].notes and by_name["provenance"].notes)
        failures += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  an untracked pinned PDF is reported "
              f"NOT-RUN, not passed")

        # This used to be the documented limit of a tree-only check: a
        # consistently renamed asset is indistinguishable from the 32 legitimate
        # soft-masked ones, so it could only be reported. The manifest records
        # each file's own digest, so it is now caught, and the assertion is
        # inverted rather than deleted -- a limitation that has been fixed should
        # leave behind a test that it stays fixed.
        renamed = pathlib.Path(tmp) / "renamed"
        shutil.copytree(pristine, renamed)
        _rename_asset(renamed, reseal=True)
        pool = {r.name: r for r in run_checks(renamed / "forms",
                                              renamed / "review-findings.json")}["shared-pool"]
        failures += pool.ok
        print(f"  {'PASS' if not pool.ok else 'FAIL'}  a consistently renamed asset is "
              f"caught by the manifest, not reported as unverifiable")

        # The prove-phase fault that used to slip through: corrupt a composited
        # asset's bytes without touching its name. Nothing about the name changes,
        # so only the manifest can see it.
        corrupted = pathlib.Path(tmp) / "corrupted"
        shutil.copytree(pristine, corrupted)
        masked_file = corrupted / "forms" / ASSET_DIR / f"{'e' * 64}.png"
        masked_file.write_bytes(_png(b"tampered composite"))
        pool = {r.name: r for r in run_checks(corrupted / "forms",
                                              corrupted / "review-findings.json")}["shared-pool"]
        failures += pool.ok
        print(f"  {'PASS' if not pool.ok else 'FAIL'}  a corrupted soft-masked asset is "
              f"caught by its recorded digest")

        # And the manifest itself must be required, not optional.
        nomanifest = pathlib.Path(tmp) / "nomanifest"
        shutil.copytree(pristine, nomanifest)
        (nomanifest / "forms" / ASSET_MANIFEST).unlink()
        pool = {r.name: r for r in run_checks(nomanifest / "forms",
                                              nomanifest / "review-findings.json")}["shared-pool"]
        failures += pool.ok
        print(f"  {'PASS' if not pool.ok else 'FAIL'}  a missing {ASSET_MANIFEST} fails "
              f"rather than silently disabling verification")

        for label, mutate, expected in MUTATIONS:
            case = pathlib.Path(tmp) / f"case-{abs(hash(label)):x}"
            shutil.copytree(pristine, case)
            mutate(case)
            results = run_checks(case / "forms", case / "review-findings.json")
            tripped = {title for (title, _), result in zip(CHECKS, results)
                       if not result.ok}
            ok = expected in tripped
            failures += not ok
            detail = "" if ok else f" (tripped {sorted(tripped) or 'nothing'})"
            print(f"  {'PASS' if ok else 'FAIL'}  {label} -> {expected}{detail}")
            shutil.rmtree(case)

    print(f"self-test: {failures} failure(s)")
    return 1 if failures else 0


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=pathlib.Path, default=REPO / "forms",
                        help="the tree to validate (default: forms/)")
    parser.add_argument("--findings", type=pathlib.Path,
                        default=HERE / "review-findings.json",
                        help="the review ledger (default: tools/formgen/review-findings.json)")
    parser.add_argument("--verbose", action="store_true",
                        help="print every failure, not the first 12 per check")
    parser.add_argument("--self-test", action="store_true",
                        help="prove each check can fail, then stop")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.self_test:
        return self_test()
    if not args.root.is_dir():
        print(f"no such tree: {args.root}", file=sys.stderr)
        return 2

    started = time.perf_counter()
    results = run_checks(args.root, args.findings)
    return report(results, args.verbose, time.perf_counter() - started)


if __name__ == "__main__":
    raise SystemExit(main())
