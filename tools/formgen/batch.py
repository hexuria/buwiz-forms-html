#!/usr/bin/env python3
"""Run the whole formgen pipeline over every source PDF and package the result.

This is the one-shot scaffolding driver. It converts each pinned BIR PDF into a
hand-maintainable HTML/CSS bundle under forms/, then gets archived: from that
point the HTML is the artefact people edit, and this script exists only as the
record of how it was derived.

Because the HTML becomes hand-maintained, four things matter more than they
would in a normal build:

  * Every bundle carries provenance.json (source files, SHA-256, page geometry,
    generator version). Re-running the generator over an edited bundle would
    destroy those edits, so the provenance is what lets anyone tell where a
    file came from without re-deriving it.
  * The CSS is split. Declarations common to every form land in the shared
    forms/base.css; only what is genuinely form-specific lands in the form's
    own form.css, and the guide document's own rules land in guide.css.
    A fix to the page scaffolding is then one edit, not 48.
  * Fonts live once at forms/fonts/, not once per form.
  * Artwork is staged as the picture the page paints, which for the 37
    soft-masked placements is not the PDF's image stream: extract.asset_for_xref
    composites the mask. The file keeps the base stream's hash as its name, so a
    bundle written by an older run holds the wrong bytes under the right name and
    has to be overwritten rather than left alone.

Two documents per bundle
------------------------

A BIR sheet carries reference material -- ATC tables, "Guidelines and
Instructions" -- that nobody fills in, either printed below the form on the same
page or shipped as a separate PDF. guides.py decides where the cut falls; this
driver runs it between lattice and emit and then emits the bundle twice:

    index.html   the form, with any inline guide region removed
    guide.html   that region, plus any standalone guide PDF for the same form

A bundle with no guide content gets no guide.html, and nothing links to one.
Standalone guide PDFs stay out of form discovery -- converting an instruction
sheet into a fillable-looking bundle is worse than skipping it -- but they are
not discarded either: guides.py maps each one to its parent form, this driver
copies it into that bundle's guides/ directory and extracts it with the same
extractor the form uses, and emit.py reflows that extraction into guide.html.
It is not a second *form* conversion -- no lattice, no font plan, no parity
score -- it is the guide's text, in the guide document, because that is the
only version of it that prints. The pinned PDF stays beside the HTML and is
linked, and remains the exact artefact.

Regeneration cannot silently drop tracked files
-----------------------------------------------

`rm -rf forms` followed by a run once deleted forms/fonts/tinos-latin-400 and
its bold: package() stages only the faces an emitted document resolved, so when
a face went unresolved the font stopped being staged and the tracked file was
simply gone -- no error, no diff to read, just two files missing from a tree
that had just been rebuilt. Every output of this driver is committed, so any
tracked file the run does not write is either lost or orphaned. That is checked
against `git ls-files` before the first byte is written; see check_tracked_files.

Usage:
    python3 tools/formgen/batch.py --source-root ~/Downloads/forms --out forms
    python3 tools/formgen/batch.py --only 2551Q --dry-run
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import functools
import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import sys
from typing import Any, Iterable

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent

sys.path.insert(0, str(HERE))
import guides  # noqa: E402 - a sibling stage; used for its schema and its partition proof

# The 35-form conversion corpus. Anything outside this list is still converted,
# but lands under extra/ so the maintained set stays the committed scope.
CORPUS = {
    "0605", "0619E", "0619F", "1601C", "1701", "1701Q", "1702MX", "1702RT",
    "2550Q", "2551Q",
    "0620", "2551M", "1603Q", "1606", "2550-DS",
    "1600-PT", "1600-VT", "1601-FQ", "1601EQ", "1602Q", "2550M",
    "1604C", "1604F", "2316", "2000-DST", "2000-OT", "1621",
    "1701A", "1701MS", "1702EX", "1702Q", "1707A", "1709", "2200C", "2200M",
}

# Guides, instruction sheets and annexes are not fileable forms. Converting one
# produces a plausible-looking bundle for a document nobody files, which is
# worse than skipping it. They are not discarded: guides.py maps each one back
# to its parent form and this driver renders it into that form's guide.html.
NON_FORM_MARKERS = ("guide", "guidelines", "instruction", "annex")

# Revisions that the folder name does not carry and the sheet does not stamp in
# extractable text. Only entries confirmed from the document body belong here --
# an invented revision is worse than an honest "undated", because revision is
# part of a form's identity and everything downstream pins on it.
# Defined in guides.py, which this module already imports. Kept as one
# definition so the two can never disagree.
REVISION_OVERRIDES = guides.REVISION_OVERRIDES

STAGES = ("extract", "lattice", "guides", "fonts", "emit")

# What a bundle is called from the inside. These are passed to emit.py rather
# than left to match its defaults: the two documents link to each other and the
# guide embeds PDFs by relative path, so the names have to be one fact, not two
# that happen to agree.
ASSET_MANIFEST = "assets-manifest.json"
FORM_DOCUMENT = "index.html"
GUIDE_DOCUMENT = "guide.html"
GUIDE_PDF_DIR = "guides"

# The flags emit.py needs before this driver can split anything.
GUIDE_EMIT_FLAGS = frozenset({"--guide-plan", "--document"})

# The flag that turns a standalone guide PDF into printable text rather than an
# embedded viewer. Optional in the same way the naming flags are: a checkout
# whose emit.py predates it still builds every bundle, with the guide PDFs
# linked instead of converted.
GUIDE_SOURCE_FLAG = "--guide-source"


@dataclasses.dataclass
class Source:
    pdf: pathlib.Path
    code: str
    revision: str
    variant: str            # "" for the main form, else "attachment" / "conso"
    in_corpus: bool

    @property
    def slug(self) -> str:
        base = f"{self.code}-{self.revision}"
        return f"{base}-{self.variant}" if self.variant else base

    @property
    def key(self) -> str:
        return self.slug.lower()


def classify(pdf: pathlib.Path) -> Source | None:
    """Derive form identity from the folder and file name, or skip."""
    name = pdf.name.lower()
    if any(marker in name for marker in NON_FORM_MARKERS):
        return None

    folder = pdf.parent.name
    match = re.match(r"^(?P<code>\d{4}[A-Za-z-]*?)(?:v(?P<rev>\d{4}[A-Za-z]?))?$", folder)
    if not match:
        return None
    code = match.group("code").upper().rstrip("-")
    revision = (match.group("rev") or "").upper()

    if not revision:
        revision = REVISION_OVERRIDES.get(code, "")
    if not revision:
        # Folders like "1601-FQ" omit the revision; take it from the file name's
        # year. No leading word boundary: "0605version1999" has none.
        year = re.search(r"(?:19|20)\d{2}", pdf.name)
        revision = year.group(0) if year else "undated"

    variant = ""
    if "attachment" in name:
        variant = "attachment"
    elif "conso" in name:
        variant = "conso"

    return Source(pdf=pdf, code=code, revision=revision, variant=variant,
                  in_corpus=code in CORPUS)


def discover(root: pathlib.Path) -> list[Source]:
    seen: dict[str, Source] = {}
    skipped: list[pathlib.Path] = []
    for pdf in sorted(root.rglob("*.pdf")):
        source = classify(pdf)
        if source is None:
            skipped.append(pdf)
            continue
        # A duplicated folder (1600PT vs 1600-PTv2018) can offer the same form
        # twice; keep the one whose folder carried an explicit revision.
        prior = seen.get(source.key)
        if prior is None or (len(pdf.parent.name) > len(prior.pdf.parent.name)):
            seen[source.key] = source
    return sorted(seen.values(), key=lambda s: (not s.in_corpus, s.code, s.revision, s.variant))


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_images(pdf: pathlib.Path, assets: pathlib.Path) -> dict[str, str]:
    """Write every embedded XObject to <sha256>.<ext> beside the bundles.

    emit.py resolves images by content hash, so this has to run before it or the
    form gets placeholders. Naming by hash also means the seal shared by several
    forms is stored once and cannot be silently swapped for different bytes.

    The bytes come from extract.asset_for_xref(), not from doc.extract_image().
    37 placements across 19 forms declare a /SMask, and for those the base stream
    is not the picture: 1604E xref 39 is 39 compressed bytes of flat black that
    its mask shapes away to nothing, so painting the base put a black block over
    the official's "BCS/ Item:" label, and its neighbour xref 37 is the 0xD9
    decorative grey wherever the mask is opaque and /Matte black elsewhere.
    asset_for_xref composites the mask and returns PNG.

    The file *name* does not change under compositing: asset_file_name() keys it
    to the sha256 of the base stream, which is also what the IR's `sha256` and
    emit.py's assets/<sha256>.<ext> lookup carry. Verified over the corpus -- all
    37 masked bases are already `png`, so not one of the 119 asset names moves.
    That is why the write is conditional on the *bytes* and not on the file
    existing: the composited asset lands under the name the base image already
    occupies, and `if not target.exists()` would keep serving last run's black
    rectangle from a pool that is shared between forms and kept between runs.

    The count returned is how many distinct assets this PDF needs, not how many
    files this call happened to create. It reaches provenance.json, and "files
    created" is a property of the pool's history rather than of the form: every
    committed provenance.json says 0 today because that run found the pool already
    populated, and once the write became conditional on bytes the first of two
    consecutive regenerates would have said 37 and the second 0 -- a byte-identical
    forms/ is one of the gate's checks.
    """
    import fitz  # local import: only the driver needs it
    import extract  # same reason, and it exits the process when fitz is absent

    assets.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf)
    needed: dict[str, bytes] = {}
    seen: set[int] = set()
    for page in doc:
        for info in page.get_images(full=True):
            # One XObject placed on three pages is one asset. Compositing a soft
            # mask decodes two pixmaps, so ask for each xref exactly once.
            xref = int(info[0])
            if xref in seen:
                continue
            seen.add(xref)
            asset = extract.asset_for_xref(doc, xref)
            if asset is None:  # a broken XObject must not stop the form
                continue
            name, data = asset
            if needed.get(name, data) != data:
                # Unrepresentable downstream: emit.py resolves artwork by this
                # name, so one of the two pictures would be served for both.
                # extract.py's 1:1 base-stream-to-mask mapping says this cannot
                # happen for this corpus; said out loud rather than assumed,
                # because the failure would be a wrong picture on a printed form
                # instead of a crash.
                print(f"  asset name collision: {pdf.name} xref {xref} claims "
                      f"{name} with different bytes", file=sys.stderr)
                continue
            needed[name] = data
    digests: dict[str, str] = {}
    for name, data in needed.items():
        target = assets / name
        if not target.is_file() or target.read_bytes() != data:
            target.write_bytes(data)
        # The file's own digest, which its NAME cannot carry. A name is the
        # sha256 of the source PDF's base image stream -- provenance identity,
        # and the right thing to key the shared pool on. But for a soft-masked
        # placement the bytes written here are the COMPOSITE, so the name can
        # never verify them, and recomputing the composite needs the source PDF,
        # which CI does not have. 32 of 119 assets were therefore unverifiable:
        # indistinguishable from corrupted ones. Recording the composite's own
        # digest separates the two jobs -- the name says where it came from, the
        # digest says whether it is intact.
        digests[name] = hashlib.sha256(data).hexdigest()
    return digests


def run_stage(args: list[str]) -> tuple[bool, str]:
    result = subprocess.run([sys.executable, *args], capture_output=True, text=True, cwd=REPO)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()
        return False, "\n".join(tail[-4:]) if tail else f"exit {result.returncode}"
    return True, ""


@functools.lru_cache(maxsize=1)
def emit_options() -> frozenset[str]:
    """The long options emit.py actually accepts, read from its own --help.

    The emitter is developed alongside this driver, so a checkout can have the
    guide plan without the emitter that consumes it. Asking emit.py what it
    supports -- once -- means such a checkout still builds all 51 forms exactly
    as it does today instead of failing 51 times on an unknown flag, and the run
    says plainly that nothing was split.
    """
    result = subprocess.run([sys.executable, str(HERE / "emit.py"), "--help"],
                            capture_output=True, text=True, cwd=REPO)
    return frozenset(re.findall(r"--[a-z][a-z0-9-]+", result.stdout))


def guide_emission_supported() -> bool:
    return GUIDE_EMIT_FLAGS <= emit_options()


def with_supported(argv: list[str], flags: Iterable[tuple[str, str]]) -> list[str]:
    """Append the flags emit.py accepts, and quietly drop the ones it does not.

    Only the naming flags go through here. They are refinements -- emit.py's own
    defaults already spell them the same way -- so a checkout without them still
    emits a correct bundle, whereas a missing --guide-plan means no split at all
    and is reported instead of ignored.
    """
    supported = emit_options()
    for flag, value in flags:
        if flag in supported:
            argv += [flag, value]
    return argv


def stage_fonts(plan_path: pathlib.Path, fonts_dir: pathlib.Path) -> None:
    """Put every WOFF2 the plan resolved beside the HTML the emitter is about to write.

    Done here, between the fonts and emit stages, because emit.py checks that the
    files its @font-face rules point at are actually there and the check is only
    worth anything if nothing had to be staged by hand. A newly mapped family
    would otherwise render from a platform fallback while the plan went on
    reporting advances for the font we meant to ship. A face fonts.py newly
    resolves -- the glyph-131 substitution being the current case -- is staged
    here on the run that first resolves it, with no separate step to forget.

    Restaged when the bytes differ, not merely when the file is absent. A WOFF2 is
    named by family and weight rather than by content hash, so an upgraded face
    keeps its name, and "copy only if missing" would ship last version's outlines
    under this version's metrics for ever. This is the same staleness that let the
    pre-composite base images survive in the asset pool; see extract_images.
    """
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    root = pathlib.Path(plan["generator"]["fonts_root"])
    fonts_dir.mkdir(parents=True, exist_ok=True)
    for face in plan["faces"]:
        relative = face.get("font_file")
        if not relative:
            continue
        source = root / relative
        target = fonts_dir / pathlib.PurePosixPath(relative).name
        if source.is_file() and (not target.is_file()
                                 or target.read_bytes() != source.read_bytes()):
            shutil.copy2(source, target)


# ---------------------------------------------------------------------------
# guide documents
# ---------------------------------------------------------------------------


def stage_guide_pdfs(pdfs: Iterable[pathlib.Path], staging: pathlib.Path) -> None:
    """Put the standalone guide PDFs where the emitted guide document expects them.

    emit.py links each one as guides/<name> and checks the file is actually
    beside its output, so they have to be staged before the emit rather than at
    packaging time -- otherwise every guide document is emitted with a warning
    about a file this driver was always going to copy.
    """
    staging.mkdir(parents=True, exist_ok=True)
    for pdf in pdfs:
        target = staging / pdf.name
        if not target.is_file():
            shutil.copy2(pdf, target)


def stage_guide_sources(pdfs: Iterable[pathlib.Path], slug: str, code: str,
                        revision: str, work: pathlib.Path
                        ) -> tuple[list[pathlib.Path], str]:
    """Extract each standalone guide PDF with the extractor the form itself uses.

    A guide shipped as its own PDF used to be handed to the browser as a linked
    <object>, which does not print with the document around it: twelve bundles
    had a guide that could be read on screen and not put on paper. Running the
    guide PDF through extract.py makes its text available to the same reflow the
    inline guides go through, so all 29 guides become one kind of document.

    These IRs are a *reading* artefact and deliberately go no further down the
    pipeline -- no lattice, no font plan, no round-trip score. A guide has no
    fields to model and no parity to measure; what it has is text, and text is
    all the reflow reads. They land in a subdirectory so audit.py's `*.ir.json`
    sweep, which scores forms, cannot mistake one for a form.

    Returns the IR paths, or an empty list and the first error.
    """
    out: list[pathlib.Path] = []
    for index, pdf in enumerate(pdfs, 1):
        target = work / "ir" / "guides" / f"{slug}.guide-{index}.ir.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        ok, error = run_stage([str(HERE / "extract.py"), "--pdf", str(pdf),
                               "--form-code", code, "--revision", revision,
                               "--expected-sha256", sha256_file(pdf),
                               "--out", str(target)])
        if not ok:
            return [], f"{pdf.name}: {error}"
        out.append(target)
    return out, ""


def region_summary(entry: dict[str, Any], destination: str) -> dict[str, Any]:
    """What one inline region took off the form page, for provenance.json."""
    return {
        "page": entry["page"],
        "cut_y_pt": entry["cut_y_pt"],
        "reclaimed_pt": entry["reclaimed_pt"],
        "reclaimed_pct": entry["reclaimed_pct"],
        "marker": entry["marker"],
        "marker_pattern": entry["marker_pattern"],
        "moved": {
            "rules": len(entry["rule_ids"]),
            "cells": len(entry["cell_ids"]),
            "text_runs": len(entry["text_run_indices"]),
            "area_fills": len(entry["area_fill_indices"]),
            "images": len(entry["image_indices"]),
        },
        "straddlers_kept_by_form": len(entry["straddlers"]),
        "moved_to": destination,
    }


def guide_block(plan: dict[str, Any], pdfs: list[pathlib.Path],
                converted: bool) -> dict[str, Any]:
    """The bundle's guide record: what left the form pages, and where it went."""
    regions = [region_summary(entry, GUIDE_DOCUMENT) for entry in plan["inline"]]
    pcts = [r["reclaimed_pct"] for r in regions]
    origins = (["inline-region"] if regions else []) + (["standalone-pdf"] if pdfs else [])
    return {
        "document": GUIDE_DOCUMENT,
        "origins": origins,
        "moved_from_form": regions,
        "standalone_pdfs": [{"file": pdf.name, "sha256": sha256_file(pdf),
                             "linked_as": f"{GUIDE_PDF_DIR}/{pdf.name}",
                             "reflowed_into": GUIDE_DOCUMENT if converted else None}
                            for pdf in pdfs],
        "reclaimed_pt_total": round(sum(r["reclaimed_pt"] for r in regions), 2),
        "reclaimed_pct_mean": round(sum(pcts) / len(pcts)) if pcts else 0,
        "reclaimed_pct_min": min(pcts) if pcts else 0,
        "reclaimed_pct_max": max(pcts) if pcts else 0,
        "note": ("Reference material printed on the source sheet, or shipped as a separate "
                 "guide PDF. It is not part of the filed form: index.html no longer carries "
                 "the regions listed under moved_from_form. Each PDF under standalone_pdfs "
                 "was re-extracted and reflowed into guide.html so that it prints with the "
                 "document; the pinned PDF is kept beside it and linked, and remains the "
                 "exact artefact."),
    }


def convert(source: Source, work: pathlib.Path, backend: str, fonts_dir: pathlib.Path,
            source_root: pathlib.Path, guide_layout: str | None,
            fonts_root: pathlib.Path | None = None) -> dict[str, Any]:
    """Run the pipeline for one form. Returns a record, never raises."""
    slug = source.key
    ir = work / "ir" / f"{slug}.ir.json"
    layout = work / "layout" / f"{slug}.layout.json"
    plan = work / "fonts" / f"{slug}.fontplan.json"
    guide_plan = work / "guides" / f"{slug}.guide.json"
    html = work / "html" / f"{slug}.html"
    for path in (ir, layout, plan, guide_plan, html):
        path.parent.mkdir(parents=True, exist_ok=True)

    record: dict[str, Any] = {
        "slug": slug, "code": source.code, "revision": source.revision,
        "variant": source.variant, "in_corpus": source.in_corpus,
        "source_file": source.pdf.name, "sha256": sha256_file(source.pdf),
        "stage_failed": None, "error": None,
    }

    assets = work / "html" / "assets"
    asset_digests = extract_images(source.pdf, assets)
    record["images_extracted"] = len(asset_digests)
    record["asset_digests"] = asset_digests

    steps = [
        ("extract", [str(HERE / "extract.py"), "--pdf", str(source.pdf),
                     "--form-code", source.code, "--revision", source.revision,
                     "--expected-sha256", record["sha256"], "--out", str(ir)]),
        ("lattice", [str(HERE / "lattice.py"), "--ir", str(ir), "--out", str(layout)]),
        ("guides", [str(HERE / "guides.py"), "--ir", str(ir), "--layout", str(layout),
                    "--source-root", str(source_root), "--out", str(guide_plan)]),
        ("fonts", [str(HERE / "fonts.py"), "--ir", str(ir), "--out", str(plan)]
                  + (["--fonts-root", str(fonts_root)] if fonts_root else [])),
    ]
    for name, argv in steps:
        ok, error = run_stage(argv)
        if not ok:
            record["stage_failed"] = name
            record["error"] = error
            return record
        if name == "fonts":
            stage_fonts(plan, fonts_dir)

    guide = json.loads(guide_plan.read_text(encoding="utf-8"))
    standalone = [pathlib.Path(p) for p in guide["standalone_pdfs"]]
    has_guide = bool(guide["inline"] or standalone)
    splitting = has_guide and guide_emission_supported()
    guide_html = work / "html" / f"{slug}.{GUIDE_DOCUMENT}"
    guide_source_irs: list[pathlib.Path] = []
    if splitting and standalone:
        stage_guide_pdfs(standalone, guide_html.parent / GUIDE_PDF_DIR)
        if GUIDE_SOURCE_FLAG in emit_options():
            guide_source_irs, error = stage_guide_sources(
                standalone, slug, source.code, source.revision, work)
            if error:
                record["stage_failed"] = "extract-guide"
                record["error"] = error
                return record

    # The form. A bundle with nothing to split is emitted exactly as it was
    # before guides existed -- same argv, same bytes -- so the 22 forms with no
    # guide content cannot regress on a run that only touches the other 29.
    emit_argv = [str(HERE / "emit.py"), "--ir", str(ir), "--layout", str(layout),
                 "--font-plan", str(plan), "--rule-backend", backend, "--out", str(html)]
    if splitting:
        emit_argv += ["--guide-plan", str(guide_plan), "--document", "form"]
        with_supported(emit_argv, [("--guide-href", GUIDE_DOCUMENT)])
    ok, error = run_stage(emit_argv)
    if not ok:
        record["stage_failed"] = "emit"
        record["error"] = error
        return record

    # One guide document per bundle, whatever the guide is made of: emit.py
    # renders the inline regions and embeds the standalone PDFs in the same
    # document, so a form with both still gets exactly one guide.html.
    if splitting:
        guide_argv = [str(HERE / "emit.py"), "--ir", str(ir), "--layout", str(layout),
                      "--font-plan", str(plan), "--rule-backend", backend,
                      "--guide-plan", str(guide_plan), "--document", "guide",
                      "--out", str(guide_html)]
        with_supported(guide_argv, [("--form-href", FORM_DOCUMENT),
                                    ("--guide-pdf-dir", GUIDE_PDF_DIR)])
        with_supported(guide_argv, [(GUIDE_SOURCE_FLAG, str(p)) for p in guide_source_irs])
        if guide_layout:
            with_supported(guide_argv, [("--guide-layout", guide_layout)])
        ok, error = run_stage(guide_argv)
        if not ok:
            record["stage_failed"] = "emit-guide"
            record["error"] = error
            return record

    ir_data = json.loads(ir.read_text())
    layout_data = json.loads(layout.read_text())
    record.update({
        "pages": len(ir_data["pages"]),
        "paper": f"{ir_data['paper']['width_pt']}x{ir_data['paper']['height_pt']}",
        "uniform_paper": ir_data["paper"]["uniform"],
        # Every page's own size, always -- not only when they differ. A single
        # bundle "paper" cannot describe 1604-CF, whose page 3 really is
        # landscape in the source (1008x612 among three 612x1008). The tree
        # validator compared every page against the ONE bundle paper and
        # reported the faithful landscape page as a defect; a check that cannot
        # express the truth will eventually call the truth a fault. Recording it
        # unconditionally keeps one code path instead of a uniform case and a
        # special case.
        "page_papers": [f"{page['width_pt']}x{page['height_pt']}"
                        for page in ir_data["pages"]],
        "fonts": sorted({f["family"] for f in ir_data["fonts"].values()}),
        "rules": sum(p["stats"]["rules_structural"] for p in ir_data["pages"]),
        "text_runs": sum(len(p["text_runs"]) for p in ir_data["pages"]),
        "images": sum(len(p["images"]) for p in ir_data["pages"]),
        "cells": sum(len(p["cells"]) for p in layout_data["pages"]),
        "comb_cells": sum(1 for p in layout_data["pages"] for c in p["cells"] if c.get("comb")),
        "growables": [
            {"page": p["index"], "rows": g["row_count"], "capacity": g["capacity"],
             "pitch_pt": g["row_pitch_pt"]}
            for p in layout_data["pages"] for g in p.get("growable", [])
        ],
        "sources": [{"role": "form", "file": source.pdf.name, "sha256": record["sha256"]}]
                   + [{"role": "guide", "file": pdf.name, "sha256": sha256_file(pdf)}
                      for pdf in (standalone if splitting else [])],
        "guide": (guide_block(guide, standalone, bool(guide_source_irs))
                  if splitting else None),
        "guide_detected": {"inline_pages": [e["page"] for e in guide["inline"]],
                           "standalone_pdfs": [p.name for p in standalone]},
        "html_bytes": html.stat().st_size,
        "html": str(html),
        "guide_build": {"plan": str(guide_plan),
                        "html": str(guide_html) if splitting else None,
                        "pdfs": [str(p) for p in (standalone if splitting else [])]},
        "guide_source_irs": [str(p) for p in guide_source_irs],
        "font_plans": [str(plan)],
    })
    return record


STYLE_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL)
COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

# Keys that name the build tree, not the source. They are machine-dependent and
# stay out of the committed bundle.
# asset_digests is the manifest's raw material and belongs to the run, not to
# a bundle: forms/assets-manifest.json already records every digest once, and
# repeating them per bundle would be the same fact in 51 places, free to drift.
BUILD_ONLY_KEYS = ("html", "font_plans", "guide_build", "guide_source_irs",
                   "asset_digests")


def split_rules(css: str) -> list[tuple[str, str]]:
    """A stylesheet as (prelude, body) pairs, with nested at-rules kept whole.

    This used to be one regex over `sel{body}`, which cannot see nesting: it
    matched the rules *inside* `@media print{...}` and dropped the condition
    around them. A conditional rule that has lost its condition is not a
    smaller stylesheet, it is a different one. `@media print{.doc-link{display:
    none}}` became an unconditional `.doc-link{display:none}`, which hid both
    documents' navigation links on screen as well as on paper, and every
    print-only pagination rule the guide document now carries would have been
    applied to the screen the same way.

    Depth counting is enough because the generated CSS has no strings holding
    braces; comments are stripped first so one in prose cannot open a phantom
    block.
    """
    css = COMMENT_RE.sub("", css)
    out: list[tuple[str, str]] = []
    index = 0
    while True:
        brace = css.find("{", index)
        if brace < 0:
            return out
        prelude = css[index:brace].strip()
        depth = 1
        cursor = brace + 1
        while cursor < len(css) and depth:
            depth += (css[cursor] == "{") - (css[cursor] == "}")
            cursor += 1
        if prelude:
            out.append((prelude, css[brace + 1:cursor - 1].strip()))
        index = cursor


def document_rules(html_path: pathlib.Path) -> list[tuple[str, str]]:
    html = html_path.read_text(encoding="utf-8")
    return split_rules("\n".join(STYLE_RE.findall(html)))


def collect_fonts(records: list[dict[str, Any]],
                  fonts_src: pathlib.Path) -> dict[str, pathlib.Path]:
    """Every WOFF2 the font plans actually resolved, by the file name they use.

    Driven by the plans rather than by listing a directory, so a family added to
    fonts.py lands in forms/fonts/ without anyone remembering to stage it, and a
    family that stopped being used stops being copied. `fonts_src` stays as the
    fallback for a file the plan names but whose source tree is not on this
    machine, which keeps an already-populated directory working.

    A guide PDF gets its own plan, so a family that only the guide uses is
    staged too; a plan that resolved nothing contributes nothing.
    """
    found: dict[str, pathlib.Path] = {}
    for record in records:
        for plan_path in record.get("font_plans", []):
            if not pathlib.Path(plan_path).is_file():
                continue
            plan = json.loads(pathlib.Path(plan_path).read_text(encoding="utf-8"))
            root = pathlib.Path(plan["generator"]["fonts_root"])
            for face in plan["faces"]:
                relative = face.get("font_file")
                if not relative:
                    continue
                name = pathlib.PurePosixPath(relative).name
                for candidate in (root / relative, fonts_src / name):
                    if candidate.is_file():
                        found.setdefault(name, candidate)
                        break
    return found


def reroot_shared_urls(css: str, depth: str) -> str:
    """Point a bundle's stylesheet at the shared pool it actually sits above.

    base.css lives at the root of forms/, so its `url("fonts/...")` resolves to
    forms/fonts/ and is correct. A bundle's form.css is one level down and
    carried the identical relative URL, which resolves to
    forms/<bundle>/fonts/ -- 404. Every @font-face that survived the hoist into
    a bundle therefore never loaded and the browser silently substituted, which
    on the faces declared there (Arimo italic, Tinos) quietly voids the
    advance-metric proof those very fonts exist to satisfy.

    The HTML has always been rerooted on the way out; the split stylesheets were
    not, because the hoist happens after emit.py has finished with them.
    """
    for prefix in ('url("', "url('", "url("):
        for shared in ("fonts/", "assets/"):
            css = css.replace(f"{prefix}{shared}", f"{prefix}{depth}{shared}")
    return css


def link_stylesheets(html: str, depth: str, own_css: str) -> str:
    """Strip the inline <style>, link base.css plus the document's own sheet."""
    links = (f'<link rel="stylesheet" href="{depth}base.css">\n'
             f'<link rel="stylesheet" href="{own_css}">')
    html = STYLE_RE.sub("", html, count=0)
    html = html.replace("</head>", f"{links}\n</head>", 1)
    html = html.replace('url("fonts/', f'url("{depth}fonts/')
    html = html.replace("url('fonts/", f"url('{depth}fonts/")
    # Font preloads are emitted in the document head for editable fields whose
    # face has no visible printed run. They are browser URLs just like the
    # @font-face src above, so the packaged bundle must climb to the shared
    # fonts directory as well.
    html = html.replace('href="fonts/', f'href="{depth}fonts/')
    html = html.replace("href='fonts/", f"href='{depth}fonts/")
    # Artwork moved to the shared pool alongside fonts/, so every reference to
    # it has to climb out of the bundle too -- href/src in the markup and url()
    # in any inlined rule.
    for prefix in ('href="assets/', "href='assets/", 'src="assets/', "src='assets/",
                   'url("assets/', "url('assets/", "url(assets/"):
        html = html.replace(prefix, prefix.replace("assets/", f"{depth}assets/"))
    return html


def render_css(header: str, rules: Iterable[tuple[str, str]]) -> str:
    return header + "\n".join(f"{sel} {{ {body} }}" for sel, body in rules) + "\n"


# ---------------------------------------------------------------------------
# the tracked-file guard
# ---------------------------------------------------------------------------


class TrackedFileLoss(Exception):
    """This run would leave a git-tracked file under the output root unwritten."""


def tracked_files(root: pathlib.Path) -> set[pathlib.Path]:
    """Every file git tracks under `root`, as absolute paths.

    An output root outside the checkout -- a scratch tree, which is how this
    driver is run while the committed bundles are being reviewed by hand --
    makes `git ls-files` fail, and the empty set is the right answer there:
    nothing under it is committed, so nothing under it can be lost.
    """
    result = subprocess.run(["git", "ls-files", "-z", "--", str(root)],
                            capture_output=True, text=True, cwd=REPO)
    if result.returncode != 0:
        return set()
    return {(REPO / name).resolve() for name in result.stdout.split("\0") if name}


def bundle_outputs(record: dict[str, Any],
                   out_root: pathlib.Path) -> tuple[pathlib.Path, list[pathlib.Path]]:
    """One converted form's bundle directory, and every file package() puts in it.

    package() writes through this rather than repeating the paths, so the guard
    below cannot certify a set of files that differs from the set that lands.
    """
    target = out_root / ("" if record["in_corpus"] else "extra") / record["slug"]
    files = [target / "form.css", target / FORM_DOCUMENT, target / "provenance.json"]
    build = record.get("guide_build") or {}
    if build.get("html"):
        files += [target / "guide.css", target / GUIDE_DOCUMENT]
        files += [target / GUIDE_PDF_DIR / pathlib.Path(pdf).name
                  for pdf in build.get("pdfs", [])]
    return target, files


def planned_outputs(records: list[dict[str, Any]], out_root: pathlib.Path,
                    fonts_src: pathlib.Path) -> set[pathlib.Path]:
    """Every path this run is about to write under `out_root`."""
    planned = {out_root / "base.css", out_root / ASSET_MANIFEST,
               out_root / FORM_DOCUMENT}
    planned |= {out_root / "fonts" / name for name in collect_fonts(records, fonts_src)}
    for record in records:
        planned |= set(bundle_outputs(record, out_root)[1])
        assets = pathlib.Path(record["html"]).parent / "assets"
        if assets.is_dir():
            # Mirrors package()'s staging: an asset no document loads is not an
            # output, so the guard reports it as an orphan rather than
            # certifying a file the run will not write.
            keep = referenced_assets(records)
            planned |= {out_root / "assets" / path.relative_to(assets)
                        for path in assets.rglob("*")
                        if path.is_file() and path.name in keep}
    return {path.resolve() for path in planned}


ASSET_REF_RE = re.compile(r"assets/([0-9a-f]{64}\.[a-z0-9]+)")


def referenced_assets(records: list[dict[str, Any]]) -> set[str]:
    """The asset file names the emitted documents actually load.

    Read back out of the HTML, not derived from the IR, because the document is
    the only authority on what it draws: an image the guide reflow does not
    render leaves an asset in the pool that nothing loads.
    """
    names: set[str] = set()
    for record in records:
        for path in (record.get("html"), (record.get("guide_build") or {}).get("html")):
            if path and pathlib.Path(path).is_file():
                names |= set(ASSET_REF_RE.findall(
                    pathlib.Path(path).read_text(encoding="utf-8")))
    return names


def report_unreferenced_assets(records: list[dict[str, Any]]) -> None:
    """Say which staged assets no emitted document loads. Nothing is removed.

    Deciding this is what keeps the tracked-file guard honest once artwork bytes
    change. The tempting rule -- "an asset nothing references is a legitimate
    removal, unlike a font or a bundle file" -- is right about *reachability*: an
    asset is only ever reached through a document that names it by content hash,
    so an unreferenced one is dead weight. It is wrong about *cause*. The one
    unreferenced asset in this corpus is 1702Q's page-3 artwork, and it is
    unreferenced because its page was relocated into the guide region and the
    guide document does not render images -- which is the defect emit.py already
    warns about, not a picture the form stopped needing. Acting on reachability
    would have deleted a tracked file to tidy away the evidence of a bug.

    So package() still stages the whole pool, tracked assets stay planned, the
    guard stays silent for artwork, and the fact is printed instead. When an asset
    really is withdrawn, `--allow-removals` is still the deliberate override; it
    exists precisely because a removal has to be asked for.
    """
    pools = {pathlib.Path(r["html"]).parent / "assets" for r in records}
    pool = {p.name for d in pools if d.is_dir() for p in d.iterdir() if p.is_file()}
    stale = sorted(pool - referenced_assets(records))
    if stale:
        print(f"{len(stale)} staged asset(s) referenced by no emitted document "
              f"(staged anyway, not removed): {', '.join(n[:12] for n in stale)}",
              file=sys.stderr)


def drop_reason(path: pathlib.Path, out_root: pathlib.Path) -> str:
    """Why this run does not write a file that is committed under `out_root`."""
    top = path.relative_to(out_root).parts[0]
    if top == "fonts":
        return ("no font plan in this run resolved this face, so package() "
                "would not stage it")
    if top == "assets":
        return "no converted page referenced this artwork, so no bundle would stage it"
    if top == "base.css":
        return "no form converted, so there were no shared rules to hoist"
    if top == ASSET_MANIFEST:
        return ("no form converted, so there were no composited assets to record; "
                "the generic message below would be actively misleading here")
    if top == FORM_DOCUMENT:
        return ("index_page.py did not write the landing page -- it needs the batch "
                "report, so check whether that stage reported an error")
    return ("no converted bundle writes it -- its form failed, was excluded, or "
            "now converts under a different <code>-<revision> slug")


def check_tracked_files(records: list[dict[str, Any]], out_root: pathlib.Path,
                        fonts_src: pathlib.Path, *, complete_run: bool,
                        allow_removals: bool) -> None:
    """Refuse to package if a committed file under `out_root` would not be written.

    Everything this driver writes is committed, so a tracked file the run does
    not produce is a defect either way, in one of two flavours:

      lost      -- it is not on disk. Someone cleared the tree before the run
                   (`rm -rf forms`) and this run does not put it back, so the
                   next commit deletes it. This is how the two Tinos faces
                   disappeared: a face went unresolved, package() stages only
                   resolved faces, and nothing said so.
      orphaned  -- it is still on disk, untouched, but the generator has stopped
                   producing it: a bundle that changed slug, a font that stopped
                   being referenced. Nothing is destroyed today, but the file is
                   now hand-maintained fiction that no re-run can reproduce.

    Only a complete run can judge the second one -- `--only` deliberately builds
    a slice, and every bundle outside it is untouched by design rather than
    dropped -- so a restricted run checks for losses alone.

    `--allow-removals` turns the whole check into a warning. Dropping a bundle
    is a real operation (a revision that was wrong, a form withdrawn); it just
    has to be asked for, because the failure mode being guarded is precisely the
    one nobody asks for.
    """
    tracked = tracked_files(out_root)
    if not tracked:
        return
    out_root = out_root.resolve()
    planned = planned_outputs(records, out_root, fonts_src)

    losses = sorted(p for p in tracked - planned if not p.exists())
    orphans = sorted(p for p in tracked - planned if p.exists()) if complete_run else []
    if not losses and not orphans:
        return

    lines = [f"{len(losses) + len(orphans)} file(s) tracked in git under "
             f"{out_root.relative_to(REPO) if out_root.is_relative_to(REPO) else out_root}"
             " would not be written by this run:"]
    for label, paths in (("LOST (not on disk either)", losses),
                         ("ORPHANED (on disk, no longer generated)", orphans)):
        for path in paths:
            lines.append(f"  {label}  {path.relative_to(out_root)}")
            lines.append(f"      {drop_reason(path, out_root)}")
    if allow_removals:
        lines.append("--allow-removals given: packaging anyway.")
        print("\n".join(lines), file=sys.stderr)
        return
    lines.append("Nothing was written. Fix the cause, or pass --allow-removals "
                 "if these files are meant to go.")
    raise TrackedFileLoss("\n".join(lines))


class _AllNames:
    """Stands in for the referenced set when a run cannot judge it.

    A partial run (`--only`) legitimately lacks the documents that reference
    most of the pool, so pruning against it would delete artwork that is merely
    out of scope. Saying so as a type is clearer than threading a None through
    the staging loop and re-deciding there.
    """

    def __contains__(self, _name: object) -> bool:
        return True


def package(records: list[dict[str, Any]], out_root: pathlib.Path,
            fonts_src: pathlib.Path, generator_version: str, *,
            complete_run: bool = True, allow_removals: bool = False) -> dict[str, Any]:
    """Split shared CSS out, then write one self-contained bundle per form."""
    ok = [r for r in records if r["stage_failed"] is None]
    if not ok:
        return {"shared_rules": 0, "bundles": 0, "guides": 0}

    check_tracked_files(ok, out_root, fonts_src, complete_run=complete_run,
                        allow_removals=allow_removals)
    # Only a whole successful run can judge this, for the same reason it is the
    # only run that can judge an orphan: a slice leaves every other form's
    # artwork in the shared pool, and a form that failed to emit leaves its own
    # there -- unreferenced by design rather than dropped. Measured: a run with
    # 39 of 51 forms failing at the fonts stage reported 83 unreferenced assets,
    # which is a report about the failures and not about the artwork.
    if complete_run and len(ok) == len(records):
        report_unreferenced_assets(ok)

    # What the emitted documents actually load. Read back out of the HTML rather
    # than derived from the IR, because the document is the only authority on
    # what it draws. A partial run cannot judge this -- another form's artwork is
    # legitimately absent from the slice -- so it stages everything as before.
    referenced = (referenced_assets(ok) if complete_run and len(ok) == len(records)
                  else None)
    if referenced is None:
        referenced = _AllNames()

    # A declaration shared by every form belongs in base.css. Anything else is
    # form-specific. This is computed, not curated, so it cannot drift. Only the
    # *form* documents vote: a guide document is present in 29 bundles of 51, so
    # letting it vote would demote shared scaffolding out of base.css and churn
    # 51 form.css files for a document that is not the form.
    per_form = {r["slug"]: document_rules(pathlib.Path(r["html"])) for r in ok}

    counts: collections.Counter[tuple[str, str]] = collections.Counter()
    for rules in per_form.values():
        counts.update(set(rules))
    shared = {rule for rule, n in counts.items() if n == len(per_form)}

    out_root.mkdir(parents=True, exist_ok=True)
    # The composite digests, which the filenames cannot carry. An asset is named
    # for its source PDF's base image stream -- that is its provenance, and the
    # right key for a shared pool -- but a soft-masked placement's bytes on disk
    # are the composite, so 32 of 119 assets could not be told from corrupted
    # ones without re-reading a PDF that CI does not have. Two facts, two fields:
    # the name says where it came from, the digest says whether it is intact.
    manifest = {}
    for record in ok:
        for name, digest in sorted((record.get("asset_digests") or {}).items()):
            if name not in referenced:
                continue
            entry = manifest.setdefault(name, {"sha256": digest, "sources": []})
            if entry["sha256"] != digest:
                raise TrackedFileLoss(
                    f"{name}: two forms composite it to different bytes "
                    f"({entry['sha256'][:12]} vs {digest[:12]}); one picture would "
                    f"be served for both")
            entry["sources"].append(record["slug"])
    for entry in manifest.values():
        entry["sources"].sort()
    (out_root / ASSET_MANIFEST).write_text(
        json.dumps({"schema_version": 1,
                    "note": ("sha256 is of the file's own bytes, which for a "
                             "soft-masked asset differ from its name. The name is "
                             "the source PDF's base stream; this is the composite."),
                    "assets": dict(sorted(manifest.items()))}, indent=2) + "\n",
        encoding="utf-8")

    (out_root / "base.css").write_text(
        render_css("/* Shared scaffolding for every generated form.\n"
                   " * Computed as the declarations common to all forms, so editing here\n"
                   " * changes every form at once. Form-specific rules live in each\n"
                   " * bundle's form.css, guide-specific rules in its guide.css. */\n",
                   # Sorted whole, not by prelude. `shared` is a set, so rules
                   # that tie on the prelude -- several @font-face blocks, the
                   # usual case -- were left in set-iteration order, which is
                   # seeded per process: two runs over identical input emitted
                   # base.css with the faces in different orders. Today's 51-form
                   # base.css has 16 distinct preludes and no tie, so this changes
                   # none of its bytes; it stops the next shared face from
                   # reintroducing a build that cannot reproduce itself.
                   sorted(shared)),
        encoding="utf-8")

    fonts_out = out_root / "fonts"
    fonts_out.mkdir(exist_ok=True)
    for name, path in sorted(collect_fonts(ok, fonts_src).items()):
        shutil.copy2(path, fonts_out / name)

    guides_written = 0
    for record in ok:
        target, _ = bundle_outputs(record, out_root)
        target.mkdir(parents=True, exist_ok=True)
        depth = "../" * (len(target.relative_to(out_root).parts))

        own = [r for r in per_form[record["slug"]] if r not in shared]
        (target / "form.css").write_text(
            reroot_shared_urls(
                render_css(f"/* {record['code']} rev {record['revision']} -- form-specific rules.\n"
                           f" * Shared scaffolding is in {depth}base.css. */\n", own), depth),
            encoding="utf-8")
        (target / FORM_DOCUMENT).write_text(
            link_stylesheets(pathlib.Path(record["html"]).read_text(encoding="utf-8"),
                             depth, "form.css"),
            encoding="utf-8")

        # The guide document, when the bundle has one. Its stylesheet is split
        # the same way the form's is: whatever base.css already carries is
        # dropped, the rest becomes guide.css.
        build = record.get("guide_build") or {}
        guide_html = build.get("html")
        if guide_html:
            guide_own = [r for r in document_rules(pathlib.Path(guide_html))
                         if r not in shared]
            (target / "guide.css").write_text(
                reroot_shared_urls(
                    render_css(f"/* {record['code']} rev {record['revision']} -- guide-specific rules.\n"
                               f" * Shared scaffolding is in {depth}base.css; the form's own rules\n"
                               f" * are in form.css and are not loaded by the guide. */\n", guide_own),
                    depth),
                encoding="utf-8")
            (target / GUIDE_DOCUMENT).write_text(
                link_stylesheets(pathlib.Path(guide_html).read_text(encoding="utf-8"),
                                 depth, "guide.css"),
                encoding="utf-8")
            # The embedded PDFs are copied per bundle, not shared like assets/:
            # a guide belongs to one form, and a directory of every form's
            # guides would be 13 documents nobody asked this bundle for.
            for pdf in build.get("pdfs", []):
                destination = target / GUIDE_PDF_DIR
                destination.mkdir(exist_ok=True)
                shutil.copy2(pdf, destination / pathlib.Path(pdf).name)
            guides_written += 1

        # Artwork is shared, not copied per bundle, for the same reason base.css
        # is: the pool holds 119 unique images and copying it into all 51 made
        # 6,069 files. Beyond the wasted space, a hand-edit to a seal in one
        # bundle would silently diverge from the fifty other bundles drawing the
        # same seal. Names are content hashes, so sharing cannot collide.
        #
        # Only what a document actually loads. Copying the whole build pool
        # staged every image any form ever extracted, so an asset kept being
        # re-shipped 95 commits after the page that drew it stopped being
        # emitted -- present, tracked, and referenced by nothing.
        source_assets = pathlib.Path(record["html"]).parent / "assets"
        if source_assets.is_dir():
            destination = out_root / "assets"
            destination.mkdir(parents=True, exist_ok=True)
            for asset in sorted(source_assets.iterdir()):
                if asset.is_file() and asset.name in referenced:
                    shutil.copy2(asset, destination / asset.name)

        provenance = {k: v for k, v in record.items() if k not in BUILD_ONLY_KEYS}
        provenance["generator"] = generator_version
        provenance["note"] = (
            "Generated once from the pinned source PDF, then maintained by hand. "
            "Re-running the generator over this directory would discard manual edits."
        )
        (target / "provenance.json").write_text(
            json.dumps(provenance, indent=2) + "\n", encoding="utf-8")

    # The landing page, written here rather than by hand. It used to be produced
    # only by running index_page.py directly, so every `rm -rf forms` regenerate
    # silently removed it and left the tree serving a raw directory listing --
    # which reads exactly like a broken server. A tracked output of this driver
    # has to be written by this driver.
    write_index_page(out_root)

    return {"shared_rules": len(shared), "bundles": len(ok), "guides": guides_written}


def write_index_page(out_root: pathlib.Path) -> None:
    """Render forms/index.html, tolerating an absent audit.

    index_page.py already reports an unaudited form honestly, so a run with no
    build/audit.json produces a page whose status column says so rather than one
    carrying the last run's numbers as if they were fresh.
    """
    ok, output = run_stage([str(HERE / "index_page.py"),
                            "--batch-report", str(REPO / "build/batch-report.json"),
                            "--audit", str(REPO / "build/audit.json"),
                            "--out", str(out_root / FORM_DOCUMENT)])
    if not ok:
        print(f"  index page not written: {output.strip().splitlines()[-1:]}",
              file=sys.stderr)


def guide_totals(records: list[dict[str, Any]]) -> dict[str, int]:
    """How the guide documents split by origin, for the run's closing summary."""
    inline = standalone = both = 0
    for record in records:
        origins = set((record.get("guide") or {}).get("origins", []))
        inline += origins == {"inline-region"}
        standalone += origins == {"standalone-pdf"}
        both += len(origins) > 1
    return {"inline": inline, "standalone": standalone, "both": both,
            "total": inline + standalone + both}


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-root", type=pathlib.Path,
                        default=pathlib.Path.home() / "Downloads/forms")
    parser.add_argument("--out", type=pathlib.Path, default=REPO / "forms")
    parser.add_argument("--work", type=pathlib.Path, default=REPO / "build")
    parser.add_argument("--fonts-dir", type=pathlib.Path,
                        default=REPO / "build/html/fonts")
    parser.add_argument("--rule-backend", choices=("svg", "css"), default="svg")
    parser.add_argument("--guide-layout", choices=("absolute", "reflow"), default=None,
                        help="How guide.html lays its content out (default: emit.py's).")
    parser.add_argument("--only", action="append", default=None,
                        help="Restrict to these form codes (repeatable).")
    parser.add_argument("--dry-run", action="store_true",
                        help="List what would be converted and stop.")
    parser.add_argument("--allow-removals", action="store_true",
                        help="Package even though committed files under --out would "
                             "not be written by this run (a dropped bundle, a font "
                             "that stopped being used). Off by default: the same "
                             "condition is how a regeneration deletes tracked files.")
    parser.add_argument("--report", type=pathlib.Path, default=None)
    parser.add_argument("--fonts-root", type=pathlib.Path, default=None,
                        help="Checkout holding node_modules/@fontsource-variable "
                             "(passed to fonts.py; default: fonts.py walks ancestors).")
    args = parser.parse_args(list(argv) if argv is not None else None)

    discovered = discover(args.source_root)
    sources = discovered
    if args.only:
        wanted = {code.upper() for code in args.only}
        sources = [s for s in sources if s.code in wanted]

    corpus = [s for s in sources if s.in_corpus]
    extra = [s for s in sources if not s.in_corpus]
    print(f"discovered {len(sources)} fileable PDFs "
          f"({len(corpus)} in corpus, {len(extra)} extra)", file=sys.stderr)
    missing = CORPUS - {s.code for s in corpus}
    if missing:
        print(f"corpus forms with no source PDF ({len(missing)}): "
              f"{', '.join(sorted(missing))}", file=sys.stderr)

    # Every skipped non-form maps to a parent form; one with no built form has
    # nowhere to go, and silently dropping it is how a guide goes missing. The
    # check reads the whole discovery, not the --only subset, so restricting a
    # run cannot invent orphans.
    standalone = guides.standalone_guide_pdfs(args.source_root)
    built = {f"{s.code}-{s.revision}".upper() for s in discovered}
    orphans = [(key, p) for key, paths in standalone.items() for p in paths if key not in built]
    print(f"{sum(len(v) for v in standalone.values())} standalone guide PDFs "
          f"for {len(standalone)} form keys", file=sys.stderr)
    for key, path in orphans:
        print(f"  guide with no built form: {key:<14} {path.name}", file=sys.stderr)

    if args.dry_run:
        for s in sources:
            tag = "corpus" if s.in_corpus else "extra "
            guide = standalone.get(f"{s.code}-{s.revision}".upper(), [])
            note = f"  + guide {guide[0].name}" if guide else ""
            print(f"  {tag}  {s.slug:<22} {s.pdf.parent.name}/{s.pdf.name}{note}")
        return 0

    if not guide_emission_supported():
        print("emit.py does not accept "
              f"{' and '.join(sorted(GUIDE_EMIT_FLAGS))}: guides are detected and "
              "planned, but nothing is split and no guide.html is written.",
              file=sys.stderr)

    records = []
    for i, source in enumerate(sources, 1):
        record = convert(source, args.work, args.rule_backend, args.fonts_dir,
                         args.source_root, args.guide_layout, args.fonts_root)
        records.append(record)
        status = "ok" if record["stage_failed"] is None else f"FAIL@{record['stage_failed']}"
        guide = record.get("guide") or {}
        note = f"  guide {guide['document']}" if guide else ""
        print(f"[{i:>2}/{len(sources)}] {source.slug:<24} {status}{note}", file=sys.stderr)

    generator = f"tools/formgen batch.py (rule-backend={args.rule_backend})"
    try:
        summary = package(records, args.out, args.fonts_dir, generator,
                          complete_run=not args.only, allow_removals=args.allow_removals)
    except TrackedFileLoss as loss:
        print(f"\nrefusing to package: {loss}", file=sys.stderr)
        return 2

    failed = [r for r in records if r["stage_failed"]]
    totals = guide_totals(records)
    print(f"\nconverted {summary['bundles']}/{len(records)}; "
          f"{summary['shared_rules']} shared CSS rules hoisted to base.css", file=sys.stderr)
    print(f"{summary['guides']} bundles gained a guide document "
          f"({totals['inline']} from an inline region, {totals['standalone']} from a "
          f"standalone PDF, {totals['both']} from both)", file=sys.stderr)
    for r in failed:
        print(f"  FAILED {r['slug']:<24} at {r['stage_failed']}: "
              f"{(r['error'] or '')[:120]}", file=sys.stderr)

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
