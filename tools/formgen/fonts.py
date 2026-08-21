#!/usr/bin/env python3
"""Resolve every PDF font in the IR to a shipped CSS face, and prove the metrics.

WHY THIS EXISTS
---------------
The old pipeline rasterised the official PDF and pixel-diffed it. Because the
source PDFs do not embed all their primary faces, Poppler substituted glyph
outlines and ~57% of the residual became outline *shape* -- unwinnable. This
module replaces that test with the one that actually governs layout:
**advance width equality**. If our CSS face advances the pen by the same
distance the PDF did, for every glyph at every size, then every line breaks at
the same place, every column lands on the same rule, and the only thing left
differing is outline shape -- which nothing in this pipeline measures and
nothing needs to.

Advance equality is checkable exactly, with no raster anywhere.

WHAT IT MEASURES, AND AGAINST WHAT
----------------------------------
The IR gives two different widths per run and they answer different questions:

  * `char_widths_pt[i]` is MuPDF's per-glyph advance box: the *source font's own*
    advance for that glyph at that size, straight out of the PDF's /Widths (or
    the embedded hmtx). It is untouched by Tc/TJ. Comparing our CSS face against
    this is the true face-identity test, so it is what `metric_check` reports.
  * `measured_advance_pt` is the distance the pen actually travelled, which also
    contains the generator's tracking (this PDF uses both `Tc` operators and
    per-glyph `TJ` adjustments). Comparing a face against *that* would report a
    34pt "error" for a face that is glyph-for-glyph identical. Tracking is
    carried into CSS `letter-spacing` instead, and `tracking_check` reports the
    residual once it has been.

The IR's own `letter_spacing_pt` / `natural_advance_pt` are null for all 310
runs of 2551Q: extract.py calls `fitz.Font(fontname="Arial,Bold")`, which MuPDF
cannot resolve, so it silently gives up. This module recomputes both from the
real shipped face, which is the only face whose numbers matter anyway.

ARIAL NARROW IS ARIAL, HORIZONTALLY SCALED
------------------------------------------
Arial Narrow used to be mapped to Roboto Condensed, and the plan honestly
reported it as NOT metric-compatible: max per-glyph delta 1.358pt (0.1435em),
mean 0.263pt, 0 of 800 samples exact at PDF precision, ~9.9% too wide. That is a
real defect, not a rounding artefact -- Roboto Condensed is an independently
drawn design that happens to be condensed.

Measuring the two real faces (`/System/Library/Fonts/Supplemental/Arial.ttf` and
`Arial Narrow.ttf`) shows Arial Narrow is not an independent design at all where
advances are concerned: **every** glyph advance is the same constant multiple of
Arial's. Over the 51 distinct glyphs 2551Q actually sets in Arial Narrow the
ratio spans 0.819322..0.820738 about a mean of 0.820054 -- a maximum deviation of
0.00073, i.e. 0.007pt at the 9.48pt this document uses, the same order as the
Arimo-vs-Arial residual and far inside the 0.10pt advance tolerance.

So Arial Narrow resolves to the **same Arimo face** as Arial, with a horizontal
scale factor. This is exactly what the PDF's own `Tz` (horizontal scaling)
operator does, and it collapses the per-glyph delta from 1.358pt to 0.0058pt.

Outline *shape* is not preserved by this: a linear condensation of Arial is not
the same drawing as Arial Narrow's own. That is deliberate and is the same
trade this module already states at the top -- nothing in this pipeline measures
outline shape, and advance equality is what governs layout.

Two CSS consequences that are easy to get wrong, both encoded in the emitted
`css` block rather than left to the caller:

  * `transform` does not apply to non-replaced **inline** boxes. A scaled run
    must be `inline-block` (or absolutely positioned / block) or the browser
    silently drops the transform and the run renders 22% too wide.
  * `letter-spacing` inside a scaled box is scaled too. The authored value is
    therefore pre-divided by the scale; `css["letter-spacing"]` is what to write
    into the stylesheet, `letter_spacing_pt` is what it comes out as on paper.

THE SERIF: TIMES NEW ROMAN RESOLVES TO TINOS
--------------------------------------------
Times New Roman is not incidental in this corpus -- it sets 69,578 characters
across 1,349 runs under that spelling and 15,923 more under the /BaseFont
spelling "TimesNewRoman", which is the same design. It used to be left
UNRESOLVED because no serif was bundled, so all of that text fell through to
whatever serif the browser had and no advance in the plan described it.

It now maps to Tinos, the metric clone of Times New Roman from the same Chrome
OS core-fonts commission as Arimo, on the same Apache-2.0 terms. The mapping is
subject to the same per-glyph proof as Arial: the PDF's own /Widths for Times
New Roman are the reference, and the self-test refuses any used face whose worst
glyph advance exceeds the tolerance. Nothing is assumed from the name.

Tinos is the case that forced packages to be described by candidate paths rather
than one path: upstream Tinos is four static faces, not a variable one, so it
arrives as `@fontsource/tinos` with a file per weight instead of
`@fontsource-variable/<name>`. Static and variable then differ in two places
that are silent if got wrong -- `font-variation-settings` is meaningless without
an `fvar`, and an `@font-face` that claims `100 900` for a static regular makes
the browser synthesise the bold. Both are decided from the loaded face's own
`fvar`, never from the file name.

GLYPHS THE SHIPPED FACES DO NOT HAVE
------------------------------------
Advance equality only says something about a glyph the face actually contains.
Arimo has no U+25CF BLACK CIRCLE -- not in `latin`, not in `latin-ext`, and
neither do Tinos or Roboto Condensed. The whole U+25A0..U+25FF geometric-shapes
block is outside every bundled subset. Asking a face for a codepoint it lacks
returns gid 0, and gid 0 has a width (0.75em in Arimo, 0.7778em in Tinos), so
the old code compared 0.75em against Arial's real 0.604em and reported a 0.74pt
metric failure. **A .notdef width is not a measurement.** It reported a
rendering hole as a near-miss on advance, and dragged a face's verdict down for
a glyph that face was never able to draw.

Absence is now its own verdict, on its own line, with its own count:

  * missing codepoints are excluded from the advance statistics entirely -- the
    face verdict describes the glyphs the face has;
  * each one is either **substituted** from `GLYPH_SUBSTITUTIONS`, an explicit
    per-codepoint table where every entry carries measured evidence and a stated
    cost, or left **unrepresentable** and warned when nothing bundled is the
    same shape;
  * neither is ever dropped. An unrepresentable glyph is a real rendering
    problem even though it is not a metric one, so it stays visible in the plan
    and the substitution is emitted for `emit.py` to apply.

GLYPHS THE SOURCE ITSELF DOES NOT NAME
--------------------------------------
Absence has a second form, and it is worse because it looks like content. Seven
glyphs across 2550M page 4 and 2553 page 2 are drawn from a symbolic Wingdings
face with no usable ToUnicode CMap. `get_text("rawdict")` reports them as U+00A7
SECTION SIGN -- the WinAnsi meaning of the byte 0xA7, from a font that does not
use WinAnsi -- while `get_texttrace()` reports U+FFFD and glyph id 131. Nothing
downstream could tell that was a guess: the sheet printed "§" where a list
marker belongs and every count still came out right.

extract.py now carries the honest reading -- U+FFFD in `text`, plus
`unmapped_glyphs` naming the glyph id -- and this module looks the *glyph id* up
rather than the character. Wingdings glyph 131 is the same drawing the sheets
with a usable encoding spell U+F0A7, so it folds onto the table entry that
already describes it and inherits that entry's verdict. See
SYMBOL_GLYPH_CODEPOINTS for the measurement, and `run_codepoints` for the fold.

U+FFFD itself must never be looked up in a face. Arimo and Tinos both *contain*
U+FFFD, so a plain coverage test calls it present and then measures its 0.8403em
placeholder against the source glyph's own advance -- a fabricated ~0.4pt
"metric error" for a character nobody set. UNSTATED_CODEPOINTS settles that
before any face is asked, the same way `has()` settles ordinary coverage.

WORD SPACING IS NOT LETTER SPACING
----------------------------------
Tracking used to be one number per run: (measured advance - natural advance),
spread over every gap as `letter-spacing`. That is right for a run the generator
*tracked* and wrong for a run it *justified*, because justification widens the
spaces and leaves the letters alone. 2553 page 2 is the case that shows it. Run
22 needs +1.201pt per gap under the single-number model -- 0.1221em of tracking
on every glyph of a 9.84pt line, which is exactly what "tracked out" looks
like -- when the truth is +0.049pt of tracking and +6.921pt on each of its seven
spaces.

The two are separable from data the IR already carries. `char_advances_pt[i]` is
an origin-to-origin distance, so it holds the generator's Tc, Tw and TJ for that
one gap; the residual against our face's advance is therefore measurable *per
gap* instead of only in total. Grouping those residuals by whether the gap
follows a word separator splits Tw from Tc, and CSS reproduces each with the
property that means it: `letter-spacing` for the letters, `word-spacing` for the
separators.

Two things keep this from being a trade:

  * Both candidates are evaluated as they would be **emitted** -- gated and
    rounded -- and the separated pair is taken only when it does not increase
    the run's worst accumulated glyph-origin error. No run's geometry can come
    out worse than it was.
  * That error is reported rather than summarised away, per run
    (`origin_drift_pt`, with `origin_drift_pt_if_uniform` beside it) and per
    face. It is the quantity verify.py compares, and it is not the same as
    `width_residual_pt`: a spacing too small before a word gap and too large
    after it cancels in the total width while leaving an interior glyph points
    away from its origin. One CSS pair cannot reproduce a line whose word gaps
    genuinely differ from each other, and where it cannot, the plan says by how
    much instead of implying it fits.

The case CSS cannot express at all is a run that opens with indentation spaces
and then justifies: `word-spacing` applies to every separator in the box, so
widening the interior gaps widens the indent too. Measured on 2553 p2 r59, the 8
leading spaces carry +0.07pt each while the interior spaces carry +0.90..+1.14,
and no (letter, word) pair fits both. Those runs keep the single-number model --
through the guard above, not through a rule about leading spaces -- and taking
the run origin after the indent is emit.py's half of the fix.

HOW WE GET THE SHIPPED FACE'S METRICS
-------------------------------------
The repo ships Arimo and Roboto Condensed as variable WOFF2, and MuPDF
cannot open WOFF2 ("unknown file format"), so `fitz.Font` is not usable for the
CSS side. We therefore read the WOFF2 directly: the table directory is plain,
and `hmtx`/`cmap`/`head`/`hhea`/`fvar`/`avar`/`HVAR` are all stored
*untransformed* (only `glyf`/`loca` carry the WOFF2 transform, and we never need
outlines). That gives exact advances for any weight on the `wght` axis, which is
what a browser would use. The brotli step has no stdlib equivalent, so it is
tried through four independent local providers and the plan records which one
answered; if none does, the plan is emitted with the metric proof explicitly
marked unavailable rather than silently assumed.

Usage:
    python3 tools/formgen/fonts.py --ir build/ir/2551q-2018.ir.json \
        --out build/fonts/2551q-2018.fontplan.json --summary
    python3 tools/formgen/fonts.py --self-test
"""

from __future__ import annotations

import argparse
import collections
import ctypes
import ctypes.util
import json
import pathlib
import struct
import subprocess
import sys
from typing import Any, Iterable, Sequence

SCHEMA_VERSION = 1

# The IR quantises every coordinate to 2dp, so a per-glyph advance read back out
# of it carries up to half a unit in the last place of intrinsic noise.
IR_QUANTISATION_PT = 0.005

# A PDF font's width table is integer thousandths of an em (/Widths, or /W for
# the Type0 faces here), so the PDF cannot express an advance more finely than
# half a per-mille of the point size. Arial's real 't' is 277.832/1000 em and
# the PDF stores 278; Arimo's is 277.832 too. The residual this module measures
# is therefore the PDF's own precision, not a difference between the faces --
# so the tolerance has to scale with size, not be a flat number.
PDF_WIDTH_HALF_PER_MILLE = 0.0005

# Emit CSS letter-spacing when a gap moves by this much...
LETTER_SPACING_EPSILON_PT = 0.01
# ...or when spacing below that floor still accumulates to this across the run.
# A 131-glyph run at 0.009pt of tracking per gap drifts 1.2pt if it is dropped,
# which is a whole character of error at 8pt.
LETTER_SPACING_ACCUMULATED_PT = 0.05

# word-spacing is gated by the same two numbers, against its own count of word
# separators rather than the run's gap count. The reasoning is identical -- a
# spacing too small to see still accumulates -- and giving it a second pair of
# constants would only invite the two to drift apart.

# The CSS word-separator characters, restricted to the one the corpus contains.
# Sweeping all 17,983 text runs of all 51 forms finds 76,250 U+0020 and no other
# whitespace or separator codepoint at all. The set is named rather than inlined
# so that adding one is a visible edit, and UNMODELLED_WORD_SEPARATORS makes a
# form that carries one of the others fail loudly: a separator this module did
# not know about would land in the letter group and have its word gap smeared
# back across the glyphs, which is the very defect this model exists to fix.
WORD_SEPARATORS = frozenset({0x0020})
UNMODELLED_WORD_SEPARATORS = frozenset({
    0x00A0,   # NO-BREAK SPACE
    0x1361,   # ETHIOPIC WORDSPACE
    0x10100, 0x10101,  # AEGEAN WORD SEPARATOR LINE / DOT
    0x1039F,  # UGARITIC WORD DIVIDER
    0x1091F,  # PHOENICIAN WORD SEPARATOR
})

# A run keeps the single-number model unless separating letter from word spacing
# leaves its worst accumulated glyph-origin error no larger. Compared with this
# much slack rather than exactly, so a tie goes to the more faithful description
# instead of to the last bit of a float sum.
SPACING_CHOICE_SLACK_PT = 1e-9

# Accumulated glyph-origin error worth telling a reader about, per face. Set to
# verify.py's own position tolerance: below it every glyph in the run lands where
# the PDF put it, and above it at least one does not.
ORIGIN_DRIFT_WARN_PT = 0.25

# Tracking tighter than this is condensed type, not incidental kerning. It is
# worth flagging because the usual cause is a wrongly chosen (too wide) face --
# though on 2551Q the per-glyph check clears the face, so it is genuine.
CONDENSED_TRACKING_EM = -0.05

# A face passes metric compatibility only if no single glyph advance differs by
# more than this. Set just above IR quantisation: nothing but rounding fits.
METRIC_COMPATIBLE_MAX_DELTA_PT = 0.05

# The self-test holds every *used* face to a tighter bar than the plan's own
# verdict threshold. With the Narrow scale in place nothing on 2551Q comes
# within a factor of 1.7 of it, so a regression shows up as a failure and not as
# a quietly-widened tolerance.
SELF_TEST_MAX_DELTA_PT = 0.01

# Arial Narrow's advances are a constant multiple of Arial's. Measured with
# fitz.Font on the macOS system faces as the width-weighted total-advance ratio
# over printable ASCII (see derive_horizontal_scale); pinned here so the
# pipeline produces byte-identical output on machines that have no Arial at all.
# Runtime derivation on this machine agrees to 5e-6, which is 5e-5 pt at 9.48pt.
ARIAL_NARROW_HORIZONTAL_SCALE = 0.820047

# Fixed, order-stable glyph set for runtime derivation. Printable ASCII only:
# it is what the form sets, and freezing it keeps the derived number reproducible.
SCALE_DERIVATION_CHARS = tuple(chr(code) for code in range(0x20, 0x7F))

# Derived scales are rounded here before use. Six places is ~1e-5 pt at these
# sizes -- below every tolerance in this module -- and makes the value printable
# and diffable rather than a float tail that shifts with the fitz build.
SCALE_DECIMALS = 6

MACOS_ARIAL = pathlib.Path("/System/Library/Fonts/Supplemental/Arial.ttf")
MACOS_ARIAL_NARROW = pathlib.Path("/System/Library/Fonts/Supplemental/Arial Narrow.ttf")


# ---------------------------------------------------------------------------
# brotli, without a brotli module
# ---------------------------------------------------------------------------


def _brotli_via_module(payload: bytes) -> bytes | None:
    try:
        import brotli  # type: ignore
    except ImportError:
        return None
    return brotli.decompress(payload)


def _brotli_via_ctypes(payload: bytes) -> bytes | None:
    """Call libbrotlidec directly if the shared library is installed."""
    candidates = [ctypes.util.find_library("brotlidec")]
    candidates += ["/opt/homebrew/lib/libbrotlidec.dylib",
                   "/usr/local/lib/libbrotlidec.dylib",
                   "libbrotlidec.so.1"]
    for name in candidates:
        if not name:
            continue
        try:
            lib = ctypes.CDLL(name)
        except OSError:
            continue
        decode = lib.BrotliDecoderDecompress
        decode.restype = ctypes.c_int
        # A font table set is small; grow only until the decoder stops asking.
        for capacity in (1 << 21, 1 << 24, 1 << 27):
            out = ctypes.create_string_buffer(capacity)
            size = ctypes.c_size_t(capacity)
            if decode(ctypes.c_size_t(len(payload)), payload,
                      ctypes.byref(size), out) == 1:
                return out.raw[:size.value]
        return None
    return None


def _brotli_via_cli(payload: bytes) -> bytes | None:
    try:
        done = subprocess.run(["brotli", "-d", "-c"], input=payload,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except (OSError, ValueError):
        return None
    return done.stdout if done.returncode == 0 else None


def _brotli_via_node(payload: bytes) -> bytes | None:
    """Node's zlib has brotli built in, and node is this repo's own toolchain."""
    script = ("const c=[];process.stdin.on('data',d=>c.push(d))"
              ".on('end',()=>process.stdout.write("
              "require('zlib').brotliDecompressSync(Buffer.concat(c))));")
    try:
        done = subprocess.run(["node", "-e", script], input=payload,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except (OSError, ValueError):
        return None
    return done.stdout if done.returncode == 0 else None


BROTLI_PROVIDERS = (
    ("python-brotli", _brotli_via_module),
    ("libbrotlidec", _brotli_via_ctypes),
    ("brotli-cli", _brotli_via_cli),
    ("node-zlib", _brotli_via_node),
)


class BrotliUnavailable(RuntimeError):
    pass


def brotli_decompress(payload: bytes) -> tuple[bytes, str]:
    """Decompress, returning the plaintext and the provider that produced it."""
    for name, provider in BROTLI_PROVIDERS:
        try:
            out = provider(payload)
        except Exception:  # noqa: BLE001 - a broken provider must not mask the next
            out = None
        if out:
            return out, name
    raise BrotliUnavailable(
        "no brotli decompressor available (tried "
        + ", ".join(n for n, _ in BROTLI_PROVIDERS) + ")")


# ---------------------------------------------------------------------------
# WOFF2 container
# ---------------------------------------------------------------------------

# Table tags addressable by 6-bit index in a WOFF2 directory, in spec order.
WOFF2_KNOWN_TAGS = (
    "cmap", "head", "hhea", "hmtx", "maxp", "name", "OS/2", "post", "cvt ",
    "fpgm", "glyf", "loca", "prep", "CFF ", "VORG", "EBDT", "EBLC", "gasp",
    "hdmx", "kern", "LTSH", "PCLT", "VDMX", "vhea", "vmtx", "BASE", "GDEF",
    "GPOS", "GSUB", "EBSC", "JSTF", "MATH", "CBDT", "CBLC", "COLR", "CPAL",
    "SVG ", "sbix", "acnt", "avar", "bdat", "bloc", "bsln", "cvar", "fdsc",
    "feat", "fmtx", "fvar", "gvar", "hsty", "just", "lcar", "mort", "morx",
    "opbd", "prop", "trak", "Zapf", "Silf", "Glat", "Gloc", "Feat", "Sill",
)


def read_uint_base128(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for _ in range(5):
        byte = data[offset]
        offset += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, offset
    raise ValueError("malformed UIntBase128")


def read_woff2_tables(path: pathlib.Path) -> tuple[dict[str, bytes], str]:
    """Return {tag: bytes} for every table, plus the brotli provider used.

    Only `glyf` and `loca` are ever WOFF2-transformed in the shipped files, and
    this module never touches outlines, so the tables it does read come out
    byte-identical to the original SFNT.
    """
    blob = path.read_bytes()
    signature, _flavor, _length, num_tables, _res, _sfnt, compressed_len = (
        struct.unpack(">4sIIHHII", blob[:24]))
    if signature != b"wOF2":
        raise ValueError(f"not a WOFF2 file: {path}")

    offset = 48  # header is 48 bytes for a single (non-collection) font
    directory: list[tuple[str, int]] = []
    for _ in range(num_tables):
        flags = blob[offset]
        offset += 1
        index, transform = flags & 0x3F, (flags >> 6) & 3
        if index == 63:
            tag = blob[offset:offset + 4].decode("latin-1")
            offset += 4
        else:
            tag = WOFF2_KNOWN_TAGS[index]
        original_length, offset = read_uint_base128(blob, offset)
        stored_length = original_length
        transformed = (tag in ("glyf", "loca") and transform != 3) or (
            tag == "hmtx" and transform != 0)
        if transformed:
            stored_length, offset = read_uint_base128(blob, offset)
        directory.append((tag, stored_length))

    plain, provider = brotli_decompress(blob[offset:offset + compressed_len])
    tables: dict[str, bytes] = {}
    cursor = 0
    for tag, length in directory:
        tables[tag] = plain[cursor:cursor + length]
        cursor += length
    return tables, provider


# ---------------------------------------------------------------------------
# SFNT metrics (advance widths only -- no outlines are ever parsed)
# ---------------------------------------------------------------------------


def parse_cmap(table: bytes) -> dict[int, int]:
    """Unicode -> glyph id, from the best available subtable."""
    count = struct.unpack(">H", table[2:4])[0]
    preference = {(3, 10): 5, (0, 4): 4, (3, 1): 3, (0, 3): 2, (0, 6): 2}
    best: tuple[int, int] | None = None
    for i in range(count):
        platform, encoding, offset = struct.unpack(">HHI", table[4 + i * 8:12 + i * 8])
        score = preference.get((platform, encoding), 0)
        if best is None or score > best[0]:
            best = (score, offset)
    assert best is not None
    offset = best[1]
    fmt = struct.unpack(">H", table[offset:offset + 2])[0]
    mapping: dict[int, int] = {}

    if fmt == 4:
        seg_x2 = struct.unpack(">H", table[offset + 6:offset + 8])[0]
        segments = seg_x2 // 2
        end_at = offset + 14
        start_at = end_at + seg_x2 + 2
        delta_at = start_at + seg_x2
        range_at = delta_at + seg_x2
        for s in range(segments):
            end = struct.unpack(">H", table[end_at + s * 2:end_at + s * 2 + 2])[0]
            start = struct.unpack(">H", table[start_at + s * 2:start_at + s * 2 + 2])[0]
            delta = struct.unpack(">h", table[delta_at + s * 2:delta_at + s * 2 + 2])[0]
            range_offset = struct.unpack(">H", table[range_at + s * 2:range_at + s * 2 + 2])[0]
            if start == 0xFFFF:
                continue
            for code in range(start, end + 1):
                if range_offset == 0:
                    gid = (code + delta) & 0xFFFF
                else:
                    at = range_at + s * 2 + range_offset + (code - start) * 2
                    gid = struct.unpack(">H", table[at:at + 2])[0]
                    if gid:
                        gid = (gid + delta) & 0xFFFF
                if gid:
                    mapping[code] = gid
    elif fmt == 12:
        groups = struct.unpack(">I", table[offset + 12:offset + 16])[0]
        for g in range(groups):
            first, last, gid = struct.unpack(
                ">III", table[offset + 16 + g * 12:offset + 28 + g * 12])
            for code in range(first, last + 1):
                mapping[code] = gid + (code - first)
    else:
        raise ValueError(f"unsupported cmap format {fmt}")
    return mapping


def region_scalar(table: bytes, region_at: int, axis_count: int,
                  region: int, coords: Sequence[float]) -> float:
    """Blend factor of one variation region at a normalised design location."""
    scalar = 1.0
    base = region_at + 4 + region * axis_count * 6
    for axis in range(axis_count):
        start, peak, end = struct.unpack(">hhh", table[base + axis * 6:base + axis * 6 + 6])
        start, peak, end = start / 16384.0, peak / 16384.0, end / 16384.0
        value = coords[axis] if axis < len(coords) else 0.0
        if peak == 0.0 or value == peak:
            continue
        if value <= start or value >= end:
            return 0.0
        if value < peak:
            scalar *= (value - start) / (peak - start)
        else:
            scalar *= (end - value) / (end - peak)
    return scalar


class MetricFace:
    """Advance-width model of one variable font, read out of its WOFF2."""

    def __init__(self, path: pathlib.Path, tables: dict[str, bytes], provider: str) -> None:
        self.path = path
        self.brotli_provider = provider
        head, hhea, maxp, hmtx = tables["head"], tables["hhea"], tables["maxp"], tables["hmtx"]
        self.units_per_em = struct.unpack(">H", head[18:20])[0]
        self.num_glyphs = struct.unpack(">H", maxp[4:6])[0]
        self.hhea_ascender = struct.unpack(">h", hhea[4:6])[0] / self.units_per_em
        self.hhea_descender = struct.unpack(">h", hhea[6:8])[0] / self.units_per_em
        self.hhea_line_gap = struct.unpack(">h", hhea[8:10])[0] / self.units_per_em
        metric_count = struct.unpack(">H", hhea[34:36])[0]
        self.advances = [struct.unpack(">H", hmtx[i * 4:i * 4 + 2])[0]
                         for i in range(metric_count)]
        self.cmap = parse_cmap(tables["cmap"])
        self.axes = self._parse_fvar(tables["fvar"]) if "fvar" in tables else []
        self.avar = self._parse_avar(tables["avar"]) if "avar" in tables else []
        self.hvar = tables.get("HVAR")
        self.has_kern = self._has_feature(tables.get("GPOS"), "kern")
        self.has_liga = self._has_feature(tables.get("GSUB"), "liga")
        self._coord_cache: dict[float, tuple[float, ...]] = {}

    # -- variation model ----------------------------------------------------

    @staticmethod
    def _parse_fvar(table: bytes) -> list[tuple[str, float, float, float]]:
        axes_at, _size, axis_count, axis_size = struct.unpack(">HHHH", table[4:12])
        axes = []
        for i in range(axis_count):
            at = axes_at + i * axis_size
            tag = table[at:at + 4].decode("latin-1")
            minimum, default, maximum = struct.unpack(">iii", table[at + 4:at + 16])
            axes.append((tag, minimum / 65536.0, default / 65536.0, maximum / 65536.0))
        return axes

    @staticmethod
    def _parse_avar(table: bytes) -> list[list[tuple[float, float]]]:
        count = struct.unpack(">H", table[6:8])[0]
        maps: list[list[tuple[float, float]]] = []
        at = 8
        for _ in range(count):
            pairs_count = struct.unpack(">H", table[at:at + 2])[0]
            at += 2
            pairs = []
            for _ in range(pairs_count):
                source, target = struct.unpack(">hh", table[at:at + 4])
                at += 4
                pairs.append((source / 16384.0, target / 16384.0))
            maps.append(pairs)
        return maps

    @staticmethod
    def _has_feature(table: bytes | None, wanted: str) -> bool:
        if not table:
            return False
        feature_list = struct.unpack(">H", table[6:8])[0]
        count = struct.unpack(">H", table[feature_list:feature_list + 2])[0]
        return any(table[feature_list + 2 + i * 6:feature_list + 6 + i * 6]
                   .decode("latin-1") == wanted for i in range(count))

    @property
    def has_weight_axis(self) -> bool:
        """Whether this file answers more than one weight.

        Read off `fvar`, never assumed from the file name. It decides two things
        a static face gets wrong if variability is presumed:
        `font-variation-settings: "wght" N` is meaningless without the axis, and
        an `@font-face` that claims `100 900` for a static regular makes the
        browser synthesise the bold -- inventing advances nothing here measured.
        """
        return any(tag == "wght" for tag, _min, _default, _max in self.axes)

    def coords(self, weight: float) -> tuple[float, ...]:
        """Normalised design location for a CSS font-weight."""
        cached = self._coord_cache.get(weight)
        if cached is not None:
            return cached
        out: list[float] = []
        for index, (tag, minimum, default, maximum) in enumerate(self.axes):
            value = weight if tag == "wght" else default
            value = max(minimum, min(maximum, value))
            if value == default:
                normalised = 0.0
            elif value < default:
                normalised = (value - default) / (default - minimum)
            else:
                normalised = (value - default) / (maximum - default)
            if index < len(self.avar) and self.avar[index]:
                normalised = self._apply_avar(self.avar[index], normalised)
            out.append(normalised)
        self._coord_cache[weight] = tuple(out)
        return self._coord_cache[weight]

    @staticmethod
    def _apply_avar(pairs: Sequence[tuple[float, float]], value: float) -> float:
        for i in range(len(pairs) - 1):
            from_a, to_a = pairs[i]
            from_b, to_b = pairs[i + 1]
            if from_a <= value <= from_b:
                if from_b == from_a:
                    return to_a
                return to_a + (to_b - to_a) * (value - from_a) / (from_b - from_a)
        return value

    def _hvar_delta(self, gid: int, coords: Sequence[float]) -> float:
        table = self.hvar
        assert table is not None
        store_at, map_at = struct.unpack(">II", table[4:12])
        if map_at:
            outer, inner = self._delta_set_index(table, map_at, gid)
        else:
            outer, inner = 0, gid
        _fmt, region_rel, data_count = struct.unpack(">HIH", table[store_at:store_at + 8])
        if outer >= data_count:
            return 0.0
        region_at = store_at + region_rel
        axis_count, _region_count = struct.unpack(">HH", table[region_at:region_at + 4])
        data_at = struct.unpack(
            ">I", table[store_at + 8 + outer * 4:store_at + 12 + outer * 4])[0] + store_at
        _items, word_field, region_index_count = struct.unpack(">HHH", table[data_at:data_at + 6])
        long_words = bool(word_field & 0x8000)
        word_count = word_field & 0x7FFF
        regions = [struct.unpack(">H", table[data_at + 6 + i * 2:data_at + 8 + i * 2])[0]
                   for i in range(region_index_count)]
        wide, narrow = (4, 2) if long_words else (2, 1)
        row_length = word_count * wide + (region_index_count - word_count) * narrow
        at = data_at + 6 + region_index_count * 2 + inner * row_length
        total = 0.0
        for i in range(region_index_count):
            if i < word_count:
                delta = struct.unpack(">i" if long_words else ">h", table[at:at + wide])[0]
                at += wide
            else:
                delta = struct.unpack(">h" if long_words else ">b", table[at:at + narrow])[0]
                at += narrow
            if delta:
                total += delta * region_scalar(table, region_at, axis_count, regions[i], coords)
        return total

    @staticmethod
    def _delta_set_index(table: bytes, at: int, gid: int) -> tuple[int, int]:
        fmt, entry_format = table[at], table[at + 1]
        if fmt == 0:
            count = struct.unpack(">H", table[at + 2:at + 4])[0]
            base = at + 4
        else:
            count = struct.unpack(">I", table[at + 2:at + 6])[0]
            base = at + 6
        if count == 0:
            return 0, gid
        inner_bits = (entry_format & 0x0F) + 1
        entry_size = ((entry_format & 0x30) >> 4) + 1
        index = min(gid, count - 1)
        value = int.from_bytes(table[base + index * entry_size:
                                     base + index * entry_size + entry_size], "big")
        return value >> inner_bits, value & ((1 << inner_bits) - 1)

    # -- advances -----------------------------------------------------------

    def glyph_for(self, char: str) -> int:
        return self.cmap.get(ord(char), 0)

    def advance_units(self, gid: int, coords: Sequence[float]) -> float:
        units = float(self.advances[gid] if gid < len(self.advances) else self.advances[-1])
        if self.hvar is not None and any(coords):
            units += self._hvar_delta(gid, coords)
        return units

    def char_advance_pt(self, char: str, size_pt: float, weight: float) -> float:
        gid = self.glyph_for(char)
        return self.advance_units(gid, self.coords(weight)) * size_pt / self.units_per_em

    def text_length_pt(self, text: str, size_pt: float, weight: float) -> float:
        coords = self.coords(weight)
        units = sum(self.advance_units(self.glyph_for(c), coords) for c in text)
        return units * size_pt / self.units_per_em

    def has(self, codepoint: int) -> bool:
        """Whether this face draws `codepoint` with a glyph of its own.

        The one question `char_advance_pt` cannot answer: a face with no glyph
        for a codepoint still returns an advance, gid 0's, and that number
        describes the placeholder rather than the character. Every caller that
        is about to measure a character has to ask this first.
        """
        return codepoint in self.cmap


# ---------------------------------------------------------------------------
# Shipped-face registry
# ---------------------------------------------------------------------------

class ShippedPackage:
    """One licence-clean WOFF2 family the renderer is allowed to bundle.

    Nothing may be added here without the per-glyph advance proof below passing
    for it. Being a metric clone is a claim; `metric_check` is what licenses it.

    `css_stack` is the fallback chain, and a scaled run keeps the *same* stack on
    purpose: the transform supplies the condensation, so the fallback must be the
    wide face. Listing "Arial Narrow" in Arimo's stack would condense an
    already-condensed fallback and land at 0.67em.
    """

    __slots__ = ("key", "css_family", "css_stack")

    def __init__(self, key: str, css_family: str, css_stack: str) -> None:
        self.key = key
        self.css_family = css_family
        self.css_stack = css_stack

    def candidates(self, weight: int, style: str, subset: str) -> tuple[str, ...]:
        """node_modules-relative paths for one (weight, style, subset), in order.

        Two spellings, because fontsource publishes a family twice: as
        `@fontsource-variable/<name>` when upstream has a `wght` axis, and as
        `@fontsource/<name>` with one file per static weight when it does not.
        Arimo is the first case, Tinos the second (upstream Tinos is four
        statics), and which spelling a given checkout has installed is not this
        module's decision to make. Both are named, the first that is on disk
        wins, and the order is fixed -- so resolution is still reproducible for a
        given checkout, and a family that gains a variable release upstream needs
        no edit here.

        Whether the file that answered is actually variable is never assumed from
        this list; it is read off the loaded face's `fvar` (see
        `MetricFace.has_weight_axis`), which is the only honest source.
        """
        return (
            f"@fontsource-variable/{self.key}/files/"
            f"{self.key}-{subset}-wght-{style}.woff2",
            f"@fontsource/{self.key}/files/"
            f"{self.key}-{subset}-{weight}-{style}.woff2",
        )


# "latin-ext" is only reached for glyphs the latin subset does not cover; the
# corpus needs none of it, but the lookup order has to be fixed for determinism.
FONTSOURCE_SUBSETS = ("latin", "latin-ext")

PACKAGES = {
    "arimo": ShippedPackage(
        "arimo", "eBIRForms Arimo",
        '"eBIRForms Arimo", Arimo, Arial, Helvetica, sans-serif'),
    "roboto-condensed": ShippedPackage(
        "roboto-condensed", "eBIRForms Roboto Condensed",
        '"eBIRForms Roboto Condensed", "Roboto Condensed", "Arial Narrow", '
        'sans-serif'),
    "tinos": ShippedPackage(
        "tinos", "eBIRForms Tinos",
        '"eBIRForms Tinos", Tinos, "Times New Roman", Times, serif'),
}


class FamilyPlan:
    """How one PDF family is served, and how honest we are allowed to be about it.

    `horizontal_scale` is the CSS `scaleX` factor applied to runs in this family.
    1.0 means no transform is emitted at all, so the common case stays clean.
    """

    __slots__ = ("package", "metric_compatible", "reason", "horizontal_scale")

    def __init__(self, package: str | None, metric_compatible: bool, reason: str,
                 horizontal_scale: float = 1.0) -> None:
        self.package = package
        self.metric_compatible = metric_compatible
        self.reason = reason
        self.horizontal_scale = horizontal_scale


FAMILY_PLANS = {
    "Arial": FamilyPlan(
        "arimo", True,
        "Arimo is a metric clone of Arial (same units-per-em, same advance for "
        "every Latin glyph, same hhea ascender/descender), Apache-2.0, bundled "
        "offline. Verified per glyph below, not assumed."),
    "Arial Narrow": FamilyPlan(
        "arimo", True,
        "Arimo horizontally scaled to {scale:.6f}. Arial Narrow is not an "
        "independent design where advances are concerned: measured on the real "
        "macOS faces, every glyph advance is the same constant multiple of "
        "Arial's (0.819322..0.820738 across the 51 glyphs this form sets, max "
        "deviation 0.00073 = 0.007pt at 9.48pt). Since Arimo is a metric clone "
        "of Arial, Arimo times that constant is a metric clone of Arial Narrow, "
        "which the per-glyph check below confirms. This reproduces the PDF's own "
        "Tz horizontal-scaling operator. It was previously mapped to Roboto "
        "Condensed, an independently drawn condensed face, at a max per-glyph "
        "error of 1.358pt (0.1435em) with 0 of 800 samples exact -- that mapping "
        "is retired. Outline shape is a linear condensation of Arial rather than "
        "Arial Narrow's own drawing; this module measures advances, not outlines.",
        ARIAL_NARROW_HORIZONTAL_SCALE),
    "Times New Roman": FamilyPlan(
        "tinos", True,
        "Tinos is the metric clone of Times New Roman from the same Chrome OS "
        "core-fonts family as Arimo -- same commissioner, same brief, same "
        "Apache-2.0 licence posture as the Arimo this pipeline already bundles. "
        "The claim is not taken on faith: the per-glyph advance proof below runs "
        "against the PDF's own /Widths for Times New Roman exactly as it does for "
        "Arial, and the self-test refuses any used face whose worst glyph advance "
        "misses by more than the tolerance. This face resolves only when the "
        "package is actually installed (@fontsource/tinos, or "
        "@fontsource-variable/tinos if one is ever published); with neither on "
        "disk it stays UNRESOLVED, because substituting a sans face or an "
        "unpinned platform serif would be a silent lie about the typography."),
}

# Spellings of a family that the PDF's /BaseFont carries but that name the same
# design. Normalising here rather than adding FAMILY_PLANS entries keeps one
# plan per design, so a mapping cannot be changed for one spelling and missed
# for another. The IR's own spelling is preserved in every emitted record --
# only the plan lookup is normalised -- so a face record stays traceable to the
# /BaseFont it came from.
FAMILY_ALIASES = {
    "TimesNewRoman": "Times New Roman",
    "TimesNewRomanPSMT": "Times New Roman",
    # PostScript spellings. 1701MS is the one sheet in the corpus whose generator
    # wrote PostScript names throughout, and its seven ArialNarrow runs were the
    # last unresolved text in the corpus purely because of the missing space.
    # The style suffix is already stripped by split_font_name(), so only the
    # family stem needs an entry.
    "ArialNarrow": "Arial Narrow",
    "ArialMT": "Arial",
    "Arial-BoldMT": "Arial",
    "Arial-ItalicMT": "Arial",
    "Arial-BoldItalicMT": "Arial",
    "ArialNarrow-Bold": "Arial Narrow",
    "ArialNarrow-Italic": "Arial Narrow",
}


def plan_for(family: str) -> FamilyPlan | None:
    """The FamilyPlan governing an IR family name, through its alias if any."""
    return FAMILY_PLANS.get(FAMILY_ALIASES.get(family, family))

# Kept wired up and loadable. No family maps here today -- Arial Narrow was the
# only user and its advances proved to be Arial's -- but a genuinely independent
# condensed family in a future form has somewhere to go that is not a lie.
UNUSED_PACKAGES = ("roboto-condensed",)


def derive_horizontal_scale(narrow: pathlib.Path = MACOS_ARIAL_NARROW,
                            base: pathlib.Path = MACOS_ARIAL) -> float | None:
    """Total-advance ratio narrow/base over SCALE_DERIVATION_CHARS, or None.

    Width-weighted (sum of advances, not mean of ratios) because that is the
    quantity a line of text actually accumulates; a plain mean lets a 5-unit
    apostrophe swing the estimate as hard as a 60-unit 'm'. Returns None whenever
    the faces or fitz are missing, which is the normal case off macOS -- the
    caller then uses the pinned constant.
    """
    if not (narrow.is_file() and base.is_file()):
        return None
    try:
        import fitz  # type: ignore
    except ImportError:
        return None
    try:
        narrow_face = fitz.Font(fontfile=str(narrow))
        base_face = fitz.Font(fontfile=str(base))
    except Exception:  # noqa: BLE001 - an unreadable system face is not fatal
        return None
    narrow_total = base_total = 0.0
    for char in SCALE_DERIVATION_CHARS:
        base_advance = base_face.glyph_advance(ord(char))
        if base_advance <= 0:
            continue
        base_total += base_advance
        narrow_total += narrow_face.glyph_advance(ord(char))
    if base_total <= 0:
        return None
    return round(narrow_total / base_total, SCALE_DECIMALS)


def scale_overrides(derive: bool) -> tuple[dict[str, float], dict[str, Any]]:
    """Per-family scale overrides plus the provenance record for the plan.

    Derivation is opt-in, not automatic-when-available. Automatic would make the
    plan's contents depend on whether the machine happens to ship Arial, and
    byte-identical output for identical input is a hard rule of this pipeline.
    """
    pinned = {"source": "pinned-constant",
              "arial_narrow_scale": ARIAL_NARROW_HORIZONTAL_SCALE,
              "derivation_available": False,
              "derived_arial_narrow_scale": None,
              "base_font": None,
              "narrow_font": None,
              "note": "pinned so output is byte-identical on machines without "
                      "the system faces; pass --derive-narrow-scale to measure"}
    if not derive:
        return {}, pinned
    derived = derive_horizontal_scale()
    if derived is None:
        pinned["note"] = ("--derive-narrow-scale requested but the macOS system "
                          "faces or fitz were unavailable; fell back to the pin")
        return {}, pinned
    return {"Arial Narrow": derived}, {
        "source": "derived-from-system-faces",
        "arial_narrow_scale": derived,
        "derivation_available": True,
        "derived_arial_narrow_scale": derived,
        "base_font": str(MACOS_ARIAL),
        "narrow_font": str(MACOS_ARIAL_NARROW),
        "pinned_arial_narrow_scale": ARIAL_NARROW_HORIZONTAL_SCALE,
        # r4 would round this whole quantity away to -0.0; the point of the field
        # is to show how small it is, so it keeps the scale's own precision.
        "pin_delta": round(derived - ARIAL_NARROW_HORIZONTAL_SCALE, SCALE_DECIMALS + 2),
        "note": "width-weighted total-advance ratio over printable ASCII; this "
                "plan is machine-dependent and is not the pipeline default",
    }


def find_fonts_root(start: pathlib.Path) -> pathlib.Path | None:
    """Nearest ancestor holding node_modules/@fontsource-variable.

    A git worktree has no node_modules of its own, so this walks up into the
    main checkout. The search is a fixed upward walk, so it is deterministic.
    """
    for candidate in [start.resolve(), *start.resolve().parents]:
        if (candidate / "node_modules" / "@fontsource-variable").is_dir():
            return candidate
    return None


def npm_package_of(relative: str) -> str:
    """'@scope/name' from a node_modules-relative font path."""
    parts = pathlib.PurePosixPath(relative).parts
    return "/".join(parts[:2])


class FaceLibrary:
    """Lazily loads shipped faces and remembers which brotli provider answered."""

    def __init__(self, fonts_root: pathlib.Path) -> None:
        self.fonts_root = fonts_root
        self._cache: dict[tuple[str, int, str, str], MetricFace | None] = {}
        self.provider: str | None = None
        self.error: str | None = None

    def load(self, package: str, weight: int, style: str,
             extended: bool = False) -> MetricFace | None:
        """The shipped face for one (package, weight, style), or None.

        Weight is part of the key because a static family keeps each weight in
        its own file; a variable family answers every weight from one file and
        simply resolves both keys to it. Misses are cached too, so a package
        that is not installed is stat'ed once rather than once per face.
        """
        subset = FONTSOURCE_SUBSETS[1] if extended else FONTSOURCE_SUBSETS[0]
        key = (package, weight, style, subset)
        if key in self._cache:
            return self._cache[key]
        spec = PACKAGES.get(package)
        if spec is None:
            return None
        candidates = spec.candidates(weight, style, subset)
        path = next((self.fonts_root / "node_modules" / relative
                     for relative in candidates
                     if (self.fonts_root / "node_modules" / relative).is_file()), None)
        if path is None:
            self.error = (
                "no shipped font file for "
                + f"{package} {weight} {style} ({subset}); install "
                + " or ".join(sorted({npm_package_of(c) for c in candidates}))
                + " -- tried " + ", ".join(candidates))
            self._cache[key] = None
            return None
        try:
            tables, provider = read_woff2_tables(path)
        except BrotliUnavailable as exc:
            self.error = str(exc)
            return None
        self.provider = provider
        face = MetricFace(path, tables, provider)
        self._cache[key] = face
        return face

    def relative_path(self, face: MetricFace) -> str:
        return str(face.path.relative_to(self.fonts_root))

    def npm_package(self, face: MetricFace) -> str:
        return npm_package_of(
            str(face.path.relative_to(self.fonts_root / "node_modules")))


# ---------------------------------------------------------------------------
# Run classification
# ---------------------------------------------------------------------------


def used_faces(ir: dict[str, Any]) -> list[tuple[str, bool, bool]]:
    """Every (family, bold, italic) triple actually carried by a text run."""
    seen = {(run["family"], bool(run["bold"]), bool(run["italic"]))
            for page in ir["pages"] for run in page["text_runs"]}
    return sorted(seen)


def css_weight(bold: bool) -> int:
    return 700 if bold else 400


def face_key(family: str, bold: bool, italic: bool) -> str:
    return f"{family}|{css_weight(bold)}|{'italic' if italic else 'normal'}"


def format_pt(value: float) -> str:
    """Deterministic pt literal: fixed precision, trailing zeros stripped."""
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    if text in ("", "-0", "0"):
        return "0pt"
    return f"{text}pt"


def r4(value: float) -> float:
    return round(float(value) + 0.0, 4)


def format_scale(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def transform_css(scale: float) -> dict[str, Any]:
    """The `scaleX` block for a run, or explicit nulls when nothing is scaled.

    The keys are always present so a consumer can write the block unconditionally
    and so a diff of two plans shows a scale appearing rather than a key.

    `transform-origin` MUST resolve to the run's left edge, which is the pen
    origin the PDF started the run at. Scale about anything else (the default is
    the box centre) and every glyph in the run, including the first, shifts by
    half the width the run lost -- for a 100pt run that is 9pt of error placed
    exactly where verify.py compares glyph origins.

    The vertical component is written as the baseline for intent, but is
    genuinely immaterial: scaleX maps (x, y) to (k*x, y) whatever the origin's y
    is. Only the horizontal component can be got wrong.

    `display` is not decoration. CSS does not apply `transform` to non-replaced
    inline boxes, so a run left as plain inline silently renders unscaled -- a
    22% width error that looks like a font bug rather than a missing property.
    """
    if scale == 1.0:
        return {"transform": None, "transform-origin": None, "display": None}
    return {
        "transform": f"scaleX({format_scale(scale)})",
        "transform-origin": "0% 0%",
        "display": "inline-block",
    }


# ---------------------------------------------------------------------------
# Codepoints no bundled face contains
# ---------------------------------------------------------------------------

# Wingdings is a symbol face: what it calls "text" is font-specific byte codes,
# not Unicode. Depending on how the generator wrote /ToUnicode, one and the same
# Wingdings glyph reaches the IR either as U+F0A7 -- the private-use spelling,
# F000 + the byte -- or as bare U+00A7. The PDF proves they are the same
# drawing: across the 27 corpus occurrences both spellings carry an advance of
# 0.4577..0.4583em, which is Wingdings gid 131's own 0.4575em.
#
# Folding the bare form onto the private-use form is what stops Arimo's SECTION
# SIGN being painted where a square bullet belongs. Nothing else would catch it:
# Arimo *does* contain U+00A7, so a coverage test on the raw codepoint passes
# and renders confidently wrong. Only Wingdings is listed. Symbol is not: its
# runs reach the IR already decoded to real Unicode (its bullet arrives as
# U+2022, which every bundled face has), so re-encoding it would invent a
# problem that is not there.
SYMBOL_PUA_BASE = 0xF000
SYMBOL_ENCODED_FAMILIES = frozenset({"Wingdings"})


def normalised_codepoint(family: str, char: str) -> int:
    """The codepoint to look a character up by, after symbol-font folding."""
    codepoint = ord(char)
    if family in SYMBOL_ENCODED_FAMILIES and 0x20 <= codepoint <= 0xFF:
        return SYMBOL_PUA_BASE + codepoint
    return codepoint


class InkBox:
    """Ink extent of one glyph, in em, from the real face's `glyf` bounds.

    Pinned rather than measured at run time, and that is forced: the shipped
    WOFF2 files store `glyf` under the WOFF2 glyph transform, and this module
    decodes no outlines anywhere (see the header). The numbers below were read
    off the faces the PDFs actually name -- Arial.ttf and Wingdings.ttf in
    /System/Library/Fonts/Supplemental -- by walking loca/glyf and taking each
    glyph's xMin/yMin/xMax/yMax.

    They are used only to state the *size* cost of a substitution in the plan.
    Nothing is decided from them; every decision in this module is made from an
    advance read live out of the shipped file. Arimo draws its own outlines, so
    treat an ink figure as the design intent it was cloned to, not as a
    measurement of the bundled binary.
    """

    __slots__ = ("width_em", "height_em", "y_min_em", "y_max_em")

    def __init__(self, width_em: float, height_em: float,
                 y_min_em: float, y_max_em: float) -> None:
        self.width_em = width_em
        self.height_em = height_em
        self.y_min_em = y_min_em
        self.y_max_em = y_max_em

    @property
    def centre_y_em(self) -> float:
        return (self.y_min_em + self.y_max_em) / 2.0

    def as_record(self) -> dict[str, Any]:
        return {"width_em": r4(self.width_em), "height_em": r4(self.height_em),
                "y_min_em": r4(self.y_min_em), "y_max_em": r4(self.y_max_em),
                "centre_y_em": r4(self.centre_y_em)}


class GlyphSubstitution:
    """One codepoint no bundled face contains, and what may stand in for it.

    `replacement is None` is a decision, not a gap in the table: it says nothing
    bundled is the same shape and the character is therefore left unresolved and
    warned. Drawing a disc where the source drew a square is a change of
    typography on a tax form, and this module would rather fail loudly than
    guess quietly -- the same posture the rest of the pipeline takes towards
    substituting a face it has not proved.
    """

    __slots__ = ("name", "source_advance_em", "source_ink", "replacement",
                 "replacement_name", "replacement_advance_em", "replacement_ink",
                 "reason")

    def __init__(self, name: str, source_advance_em: float, source_ink: InkBox,
                 replacement: int | None, replacement_name: str | None,
                 replacement_advance_em: float | None,
                 replacement_ink: InkBox | None, reason: str) -> None:
        self.name = name
        self.source_advance_em = source_advance_em
        self.source_ink = source_ink
        self.replacement = replacement
        self.replacement_name = replacement_name
        # Pinned so the self-test can hold the shipped file to it. Unlike the
        # ink figures this one IS readable at run time, which is exactly why it
        # is pinned: a pin nobody checks is a comment, and this one is checked.
        self.replacement_advance_em = replacement_advance_em
        self.replacement_ink = replacement_ink
        self.reason = reason

    @property
    def resolved(self) -> bool:
        return self.replacement is not None

    def as_record(self, codepoint: int) -> dict[str, Any]:
        """The table row for this entry, filed under the codepoint it describes.

        The key is passed in rather than stored so it is written exactly once,
        in the table literal, and an entry cannot end up describing one
        codepoint while being looked up under another.
        """
        record: dict[str, Any] = {
            "from": format_codepoint(codepoint),
            "from_char": chr(codepoint),
            "from_name": self.name,
            "source_advance_em": r4(self.source_advance_em),
            "source_ink_em": self.source_ink.as_record(),
            "reason": self.reason,
        }
        if not self.resolved:
            record.update({"to": None, "to_name": None, "status": "unrepresentable"})
            return record
        assert self.replacement_ink is not None
        record.update({
            "to": format_codepoint(self.replacement),
            "to_char": chr(self.replacement),
            "to_name": self.replacement_name,
            "status": "substituted",
            "replacement_advance_em": r4(self.replacement_advance_em or 0.0),
            "advance_ratio": r4((self.replacement_advance_em or 0.0)
                                / self.source_advance_em),
            "replacement_ink_em": self.replacement_ink.as_record(),
            # The two costs of drawing a different glyph, stated as ratios so a
            # caller can correct for them: how much narrower the ink is, and how
            # far its centre sits from where the source put it.
            "ink_width_ratio": r4(self.replacement_ink.width_em / self.source_ink.width_em),
            "ink_centre_offset_em": r4(
                self.replacement_ink.centre_y_em - self.source_ink.centre_y_em),
        })
        return record


def format_codepoint(codepoint: int) -> str:
    return f"U+{codepoint:04X}"


def display_char(codepoint: int) -> str:
    """A terminal-safe rendering of one codepoint for the summary table.

    The private-use codepoints in this table exist precisely because no font
    agrees what they are, so writing one to a terminal produces a replacement
    box or nothing at all, and the line silently loses a column. JSON keeps the
    real character -- it is the data -- but the human-facing table does not.
    """
    if 0xE000 <= codepoint <= 0xF8FF or codepoint < 0x20:
        return f"<{format_codepoint(codepoint)}>"
    return chr(codepoint)


def table_status(codepoint: int) -> str:
    """This module's policy verdict on a codepoint, independent of any face.

    Used for the corpus-level tally, where occurrences arrive from resolved and
    unresolved families in whatever order the pages happen to be walked. Deriving
    the status from whichever run was seen first would make the report depend on
    page order; the policy does not.
    """
    substitution = GLYPH_SUBSTITUTIONS.get(codepoint)
    if substitution is not None and substitution.resolved:
        return SUBSTITUTED
    return UNREPRESENTABLE


BLACK_CIRCLE = 0x25CF
BULLET = 0x2022
WINGDINGS_SQUARE_BULLET = 0xF0A7

# What extract.py writes into `text` where the PDF drew a glyph whose codepoint
# the file never states. It is a marker, not a character the document asked for.
UNMAPPED_CODEPOINT = 0xFFFD

# Codepoints that may never be looked up in a face, whatever the face contains.
# Arimo and Tinos both carry U+FFFD (0.8403em of diamond-and-question-mark), so
# the ordinary coverage test calls it present and then measures that placeholder
# against the source glyph's own advance -- 0.4575em for the Wingdings square,
# i.e. a fabricated ~0.4pt "metric error" for a character nobody set. Absence has
# to be decided from what the *source* stated, not from what the substitute
# happens to draw, so this is settled in resolve_glyph before `has()` is asked.
UNSTATED_CODEPOINTS = frozenset({UNMAPPED_CODEPOINT})

# Names for absent codepoints that GLYPH_SUBSTITUTIONS does not describe, so a
# warning can say what went wrong rather than "unknown glyph". A codepoint here
# is deliberately *not* a substitution-table entry: the table's rows are claims
# about a specific drawing, and the honest claim about an unnamed glyph id is
# that we know one was drawn and do not know which.
ABSENT_GLYPH_NAMES = {
    UNMAPPED_CODEPOINT: "GLYPH THE SOURCE DOES NOT NAME (no ToUnicode, and no "
                        "entry in SYMBOL_GLYPH_CODEPOINTS for its glyph id)",
}

# (family, glyph id) -> the codepoint this module reasons about instead.
#
# Wingdings glyph 131 is the glyph the sheets with a usable encoding spell
# U+F0A7: byte 0xA7 folds to PUA F000+0xA7 by the same rule as
# SYMBOL_ENCODED_FAMILIES, and the advance agrees. Measured over all 7
# occurrences -- 2550M page 4 at 8.25pt (3.78pt) and 6.75pt (3.09pt x3), 2553
# page 2 at 9.84pt (4.51pt x3) -- the PDF records 0.4578..0.4583em against the
# 0.4575em pinned for U+F0A7 out of Wingdings' own hmtx. That is a spread of
# 0.0008em, four times inside SOURCE_ADVANCE_TOLERANCE_EM, so this is a
# re-spelling of a glyph the table already covers and not a new entry; it
# inherits U+F0A7's verdict, which is `unrepresentable`.
#
# Unrepresentable is the measurement's answer, not an omission. Asking arimo,
# tinos and roboto-condensed (latin and latin-ext, 400 and 700) for every square
# and block -- U+25A0, U+25A1, U+25A3, U+25AA, U+25AB, U+25AC, U+25AE, U+25FB..
# U+25FE, U+2586..U+2589, U+2596, U+2610..U+2612, U+220E -- returns nothing at
# all. The only filled marks any of them draw are discs: U+2022 BULLET at
# 0.3501em and U+00B7 MIDDLE DOT at 0.333em, against the 0.2891 x 0.2891em of
# square ink wanted. A disc is not a square, and on a tax form these mark
# statutory list items, so the shape is not ours to change; the plan warns
# instead. `substitution_table_failures` re-derives that absence from the shipped
# files on every self-test, so this comment cannot outlive the fonts.
SYMBOL_GLYPH_CODEPOINTS = {("Wingdings", 131): WINGDINGS_SQUARE_BULLET}


def run_codepoints(run: dict[str, Any]) -> list[int]:
    """The codepoint to look each character of a run up by, one per character.

    Two folds, answering different questions. `normalised_codepoint` re-encodes a
    symbol face's byte as the private-use codepoint the rest of this module
    reasons about. This function additionally overrules the *character* at every
    position where the IR says the source never stated one: there `text` carries
    U+FFFD and `unmapped_glyphs` names the glyph that was actually drawn.

    A position whose glyph id has no entry in SYMBOL_GLYPH_CODEPOINTS keeps
    U+FFFD and is therefore reported unrepresentable and warned about, rather
    than guessed at. That is the honest verdict: a glyph was drawn and we cannot
    say which one.
    """
    family = run["family"]
    codepoints = [normalised_codepoint(family, char) for char in run["text"]]
    for entry in run.get("unmapped_glyphs") or []:
        index = int(entry["index"])
        if 0 <= index < len(codepoints):
            codepoints[index] = SYMBOL_GLYPH_CODEPOINTS.get(
                (family, int(entry["glyph_id"])), UNMAPPED_CODEPOINT)
    return codepoints

# The package whose metrics the pinned replacement figures below describe. Every
# occurrence in this corpus is set in a sans family -- Arial, which resolves to
# Arimo, and Calibri, which has no plan yet but would not resolve to a serif --
# so Arimo's numbers are the true cost of every substitution actually applied.
# Stated rather than assumed because it is not a constant across families:
# Roboto Condensed draws U+2022 at 0.3374em against Arimo's 0.3501em, so a form
# that ever mapped an affected family there would need its own measurement.
# `substitution_self_test` asserts that no substitution escapes this package.
SUBSTITUTION_REFERENCE_PACKAGE = "arimo"

# Every codepoint the corpus sets that no bundled subset contains. Measured, not
# guessed: the sweep that produced this list asked each of arimo, tinos and
# roboto-condensed (latin and latin-ext alike) for every non-ASCII codepoint in
# all 51 forms. Only these two came back absent -- U+2013, U+2019, U+201C,
# U+201D, U+2026, U+2022, U+00A7 and U+00BD are all present and need no entry.
# The self-test re-runs both halves of that check against the shipped files, so
# this table cannot drift away from the fonts it describes.
GLYPH_SUBSTITUTIONS: dict[int, GlyphSubstitution] = {
    BLACK_CIRCLE: GlyphSubstitution(
        name="BLACK CIRCLE",
        # Arial's own advance, and the PDF agrees: all 486 corpus occurrences
        # carry 0.6032..0.6050em, whether the run calls the face Arial or
        # Calibri.
        source_advance_em=0.6040,
        source_ink=InkBox(0.4302, 0.4302, 0.0669, 0.4971),
        replacement=BULLET,
        replacement_name="BULLET",
        replacement_advance_em=0.3501,
        replacement_ink=InkBox(0.2476, 0.2476, 0.2266, 0.4741),
        reason=(
            "U+2022 BULLET is the only filled disc in any bundled subset, and a "
            "disc is what U+25CF is -- the shape is right, the size is not. The "
            "geometric-shapes block U+25A0..U+25FF is absent from arimo, tinos "
            "and roboto-condensed in both latin and latin-ext, so there is no "
            "same-size circle to reach for; the only other disc, U+00B7 MIDDLE "
            "DOT, is smaller still (0.1001em of ink against the 0.4302em "
            "wanted, 23%). Measured cost of this substitution: the disc is "
            "0.2476em across instead of 0.4302em (57.6%) and its centre sits "
            "0.0684em higher (+0.3504em against +0.2820em above the baseline). "
            "Advance is 0.3501em against 0.6040em (58.0%). Those two ratios "
            "agree to 0.7%, i.e. U+2022 is very nearly U+25CF drawn at 0.58 "
            "scale, so a caller that wants the right size can set this one "
            "glyph at font-size x 1.7252 and drop it 0.0684em x 1.7252 = "
            "0.1180em; the plan carries every number needed for that and "
            "reports the substitution either way. Not corrected here because "
            "per-glyph size and baseline are emit.py's to apply, and a caller "
            "that applies neither still gets a bullet rather than a hole."),
    ),
    WINGDINGS_SQUARE_BULLET: GlyphSubstitution(
        name="WINGDINGS SMALL BLACK SQUARE (byte 0xA7)",
        # Wingdings gid 131's own advance; the PDF's 0.4577..0.4583em across all
        # 27 occurrences is the same number through the IR's 2dp quantisation.
        source_advance_em=0.4575,
        source_ink=InkBox(0.2891, 0.2891, 0.2168, 0.5059),
        replacement=None,
        replacement_name=None,
        replacement_advance_em=None,
        replacement_ink=None,
        reason=(
            "UNRESOLVED on purpose: this is a filled square (0.2891 x 0.2891em "
            "of ink, y +0.2168..+0.5059) and no bundled subset contains a "
            "square at all. U+25AA BLACK SMALL SQUARE, U+25A0 and the rest of "
            "U+25A0..U+25FF are absent from arimo, tinos and roboto-condensed "
            "in both latin and latin-ext; the only filled marks available are "
            "discs (U+2022, U+00B7). A disc is not a square, and on a tax form "
            "these mark statutory list items and checkbox rows, so swapping the "
            "shape would be a change of typography rather than a rendering "
            "detail. Fixing this needs a bundled face that actually carries "
            "U+25AA -- a geometric-shapes subset, or the source Wingdings "
            "outline converted to SVG the way the artwork path already is -- "
            "not another row in this table."),
    ),
}

PRESENT = "present"
SUBSTITUTED = "substituted"
UNREPRESENTABLE = "unrepresentable"


class GlyphResolution:
    """What the shipped face will actually draw for one source character.

    Three outcomes, and they are measured differently on purpose:

      * `present`   -- the face has the glyph. This is the only case that may
                       enter the advance proof.
      * `substituted` -- the face lacks it and the table names a stand-in. The
                       advance is real (it is the stand-in's) but it is not
                       evidence about the face, so it is reported beside the
                       proof and never inside it.
      * `unrepresentable` -- nothing bundled draws it. There is no advance to
                       measure at all; the PDF's own width is reserved so the
                       rest of the run still lands correctly, and the hole is
                       warned about.
    """

    __slots__ = ("char", "codepoint", "status", "drawn_codepoint", "substitution")

    def __init__(self, char: str, codepoint: int, status: str,
                 drawn_codepoint: int | None,
                 substitution: GlyphSubstitution | None) -> None:
        self.char = char
        self.codepoint = codepoint
        self.status = status
        self.drawn_codepoint = drawn_codepoint
        self.substitution = substitution

    @property
    def absent(self) -> bool:
        return self.status != PRESENT


def resolve_glyph(face: MetricFace, codepoint: int, char: str) -> GlyphResolution:
    """Decide what a shipped face draws for one character, before measuring it.

    Every advance in this module goes through here first. `char_advance_pt` will
    happily answer for a codepoint the face has never heard of -- it returns
    gid 0's width -- so the coverage question has to be settled before the
    measurement, not inferred from it afterwards.

    `codepoint` is the caller's, from `run_codepoints`, rather than derived from
    `char` here. The two disagree exactly where it matters: a symbol face's byte
    folds to a private-use codepoint, and an unmapped glyph's character is a
    U+FFFD marker that names no glyph the document contains.
    """
    if codepoint in UNSTATED_CODEPOINTS:
        # Absence by construction, ahead of any coverage test: see
        # UNSTATED_CODEPOINTS for why asking the face is the wrong question.
        return GlyphResolution(char, codepoint, UNREPRESENTABLE, None, None)
    if face.has(codepoint):
        return GlyphResolution(char, codepoint, PRESENT, codepoint, None)
    substitution = GLYPH_SUBSTITUTIONS.get(codepoint)
    if substitution is not None and substitution.replacement is not None:
        if face.has(substitution.replacement):
            return GlyphResolution(char, codepoint, SUBSTITUTED,
                                   substitution.replacement, substitution)
    return GlyphResolution(char, codepoint, UNREPRESENTABLE, None, substitution)


def resolution_advance_pt(resolution: GlyphResolution, face: MetricFace,
                          size_pt: float, weight: float, pdf_width_pt: float) -> float:
    """The advance this character will occupy once the page is laid out.

    Not the same question as "what does the face say", and the difference is the
    whole point of the three statuses:

      * present     -- the face's own advance, which is also the proof.
      * substituted -- the *stand-in's* advance, because that is the glyph the
        browser will set and therefore the width the rest of the run is offset
        by. Reporting the source's width here would make the run model describe
        a layout that never happens.
      * unrepresentable -- the PDF's own width. Nothing is known about what will
        be drawn, but the width the character must occupy is known exactly, and
        reserving it keeps every later glyph in the run at its PDF origin.
        Falling through to gid 0's 0.75em instead would inject a fabricated
        error into the run's advance and into its derived letter-spacing.
    """
    if resolution.status == UNREPRESENTABLE:
        return pdf_width_pt
    assert resolution.drawn_codepoint is not None
    return face.char_advance_pt(chr(resolution.drawn_codepoint), size_pt, weight)


class AbsentGlyphTally:
    """Running count of one codepoint that a face could not draw as written.

    Kept per codepoint rather than per occurrence because the plan's job here is
    to say *what* is missing and *how much* of it there is. The sizes and
    families are carried so a reader can tell one bullet on one sheet from 405
    of them across the corpus without opening the IR.
    """

    __slots__ = ("codepoint", "char", "status", "substitution", "count",
                 "sizes_pt", "families", "example")

    def __init__(self, resolution: GlyphResolution, status: str) -> None:
        self.codepoint = resolution.codepoint
        self.char = resolution.char
        self.status = status
        self.substitution = resolution.substitution
        self.count = 0
        self.sizes_pt: set[float] = set()
        self.families: collections.Counter[str] = collections.Counter()
        self.example: str | None = None

    def record(self, size_pt: float, family: str, run_text: str) -> None:
        """Add one occurrence. Only the first run text is kept, as an example:
        405 identical previews would say nothing 1 does not."""
        self.count += 1
        self.sizes_pt.add(r4(size_pt))
        self.families[family] += 1
        if self.example is None:
            self.example = run_text[:32]

    def as_record(self) -> dict[str, Any]:
        substitution = self.substitution
        return {
            "codepoint": format_codepoint(self.codepoint),
            "char": self.char,
            "name": (substitution.name if substitution
                     else ABSENT_GLYPH_NAMES.get(self.codepoint)),
            "status": self.status,
            "count": self.count,
            "sizes_pt": sorted(self.sizes_pt),
            "families": dict(sorted(self.families.items())),
            "in_run": self.example,
            "replacement": (format_codepoint(substitution.replacement)
                            if substitution and substitution.replacement is not None
                            else None),
            "replacement_char": (chr(substitution.replacement)
                                 if substitution and substitution.replacement is not None
                                 else None),
        }


def tally_absent(store: dict[int, AbsentGlyphTally], resolution: GlyphResolution,
                 size_pt: float, family: str, run_text: str,
                 status: str | None = None) -> None:
    """Count one absent glyph into `store`, creating its tally on first sight.

    `status` overrides the per-face verdict, and the corpus-level tally passes
    `table_status` for it. Without that, the same codepoint arriving first from
    a resolved face and later from an unresolved one (or the reverse) would file
    the whole tally under whichever run the page walk happened to reach first.
    """
    tally = store.get(resolution.codepoint)
    if tally is None:
        tally = AbsentGlyphTally(resolution, status or resolution.status)
        store[resolution.codepoint] = tally
    tally.record(size_pt, family, run_text)


def absent_records(store: dict[int, AbsentGlyphTally]) -> list[dict[str, Any]]:
    """Absent-glyph tallies, ordered by codepoint so two runs serialise alike."""
    return [store[codepoint].as_record() for codepoint in sorted(store)]


# ---------------------------------------------------------------------------
# Tracking: letter spacing and word spacing, separately
# ---------------------------------------------------------------------------


def spacing_is_worth_emitting(value_pt: float, count: int) -> bool:
    """Whether a spacing of `value_pt` over `count` gaps has to be written out.

    Two thresholds, and the second is the one that earns its keep: spacing too
    small to see per gap still accumulates over the run.
    """
    return (abs(value_pt) >= LETTER_SPACING_EPSILON_PT
            or abs(value_pt) * count >= LETTER_SPACING_ACCUMULATED_PT)


def authored_spacing_pt(value_pt: float, count: int, scale: float) -> float | None:
    """The CSS value to write for a page-space spacing, or None to omit it.

    Divide before rounding, never after: both letter-spacing and word-spacing sit
    inside the scaled box and are scaled with it, so it is the quotient the
    transform lands on `value_pt`, and the quotient is what has to be
    representable at the emitted precision.
    """
    if not spacing_is_worth_emitting(value_pt, count):
        return None
    return r4(value_pt / scale)


def gated_spacing_pair(letter_pt: float, word_pt: float, gaps: int,
                       separator_gaps: int,
                       scale: float) -> tuple[float | None, float | None]:
    """The two authored values, gated together rather than one at a time.

    Each component on its own may be dropped only while it accumulates less than
    LETTER_SPACING_ACCUMULATED_PT across the run. Gating them independently would
    let two sub-threshold omissions add to twice that bound, which is exactly the
    guarantee the single-number model used to give for free -- so when their sum
    crosses it, both are written out. A spacing that really is negligible then
    appears as a negligible number, which costs nothing and keeps the bound
    provable rather than merely observed.
    """
    letter_css = authored_spacing_pt(letter_pt, gaps, scale)
    word_css = authored_spacing_pt(word_pt, separator_gaps, scale)
    dropped = ((0.0 if letter_css is not None else letter_pt * gaps)
               + (0.0 if word_css is not None else word_pt * separator_gaps))
    if abs(dropped) >= LETTER_SPACING_ACCUMULATED_PT:
        return r4(letter_pt / scale), r4(word_pt / scale)
    return letter_css, word_css


def origin_drift_pt(origin_offsets: Sequence[float], css_advances: Sequence[float],
                    separator: Sequence[bool], letter_pt: float,
                    word_pt: float) -> float:
    """Worst glyph-origin error one spacing pair leaves in a run.

    The stricter of the two costs this module reports, and not the same question
    as `width_residual_pt`. Total width can come out exact while an interior
    glyph sits points away from where the PDF put it, because a gap that is too
    narrow before a word space and too wide after it cancels in the sum.
    verify.py's `Interior` check is this same quantity measured on the round-trip,
    and its docstring records 7.44pt of it -- thirty times the position
    tolerance -- caused by the single-number model. So this is what decides
    whether a run is right.

    Compared against `char_origin_offsets_pt` rather than against a running sum
    of `char_advances_pt`, and that is not interchangeable: every advance is
    quantised to 2dp, so summing 130 of them accumulates up to 0.65pt of pure
    rounding, which is four times what this function is trying to detect.
    extract.py records each offset as a single subtraction rounded once for
    exactly this reason.
    """
    running_css = 0.0
    separators = 0
    worst = 0.0
    for index in range(len(separator)):
        running_css += css_advances[index]
        separators += 1 if separator[index] else 0
        placed = running_css + letter_pt * (index + 1) + word_pt * separators
        worst = max(worst, abs(placed - float(origin_offsets[index + 1])))
    return worst


class RunSpacing:
    """One run's emitted spacing, and what emitting it costs in glyph origins.

    Both `*_css_pt` values are authored, pre-transform numbers; the page-space
    quantities a reader compares against the IR are the properties below, which
    put the scale back. `None` means the property is omitted from the CSS
    entirely, which is not the same as zero: it says the gate decided the run
    does not need it.
    """

    __slots__ = ("letter_css_pt", "word_css_pt", "gaps", "separator_gaps",
                 "separated", "drift_pt", "uniform_drift_pt", "scale",
                 "measurable")

    def __init__(self, letter_css_pt: float | None, word_css_pt: float | None,
                 gaps: int, separator_gaps: int, separated: bool,
                 drift_pt: float | None, uniform_drift_pt: float | None,
                 scale: float, measurable: bool = True) -> None:
        self.letter_css_pt = letter_css_pt
        self.word_css_pt = word_css_pt
        self.gaps = gaps
        self.separator_gaps = separator_gaps
        self.separated = separated
        self.drift_pt = drift_pt
        self.uniform_drift_pt = uniform_drift_pt
        self.scale = scale
        self.measurable = measurable

    @property
    def letter_pt(self) -> float:
        return (self.letter_css_pt or 0.0) * self.scale

    @property
    def word_pt(self) -> float:
        return (self.word_css_pt or 0.0) * self.scale

    @property
    def applied_pt(self) -> float:
        """Total width the emitted properties add to the run, in page space."""
        return self.letter_pt * self.gaps + self.word_pt * self.separator_gaps

    @property
    def model(self) -> str:
        return "letter+word" if self.separated else "letter-uniform"


def derive_spacing(text: str, css_advances: Sequence[float],
                   origin_offsets: Sequence[float], natural_pt: float,
                   measured_pt: float, scale: float) -> RunSpacing:
    """Recover the generator's letter spacing and word spacing for one run.

    The single-number model -- (measured - natural) spread over every gap -- is
    the right description of a run that was *tracked* and the wrong description
    of one that was *justified*: justification widens the spaces, and smearing
    that residual across the letters tracks the whole line out. Splitting them
    needs a per-gap measurement, and `char_origin_offsets_pt` is one: the
    difference of two consecutive offsets is the distance the pen actually
    travelled over that gap, so subtracting our face's advance leaves the Tc, Tw
    and TJ the generator applied there and nothing else.

    Offsets, not `char_advances_pt`. The advances are individually correct and
    quantised to 2dp, so a running sum of 130 of them carries up to 0.65pt of
    accumulated rounding -- larger than the word gaps this is trying to find, and
    the reason an earlier draft of this function reported a 0.145pt width miss on
    a run whose real error was 0.0005pt. Each offset is one subtraction rounded
    once.

    Group the residuals by whether the gap follows a word separator and the two
    means are the two CSS properties. Both candidate pairs are then scored as
    they would be emitted -- through the gate, through the rounding -- and the
    separated pair is taken only if it leaves no glyph further from its origin
    than the single number does. So this can improve a run and cannot cost one,
    and the runs it cannot improve are named by the cost it reports rather than
    by a rule about what they look like.

    A run with no separator, or with nothing but separators, has no split to
    make and keeps the single number byte-for-byte -- which is most short field
    labels in the corpus, and is why this change moves only the runs it means to.
    """
    gaps = len(text) - 1
    if gaps <= 0:
        return RunSpacing(None, None, 0, 0, False, 0.0, 0.0, scale)

    uniform_pt = (measured_pt - natural_pt) / gaps
    separator = [ord(char) in WORD_SEPARATORS for char in text[:gaps]]
    separator_gaps = sum(separator)
    uniform_css = authored_spacing_pt(uniform_pt, gaps, scale)

    # Without exact origin offsets there is nothing to group by that is not
    # mostly quantisation. An IR old enough to lack the field falls back to the
    # single number and says so, exactly as verify.py does: re-extract, do not
    # lean on a fallback that reports rounding as a defect.
    measurable = (len(origin_offsets) == len(text)
                  and len(css_advances) == len(text))
    if not measurable:
        return RunSpacing(uniform_css, None, gaps, separator_gaps, False,
                          None, None, scale, measurable=False)

    residuals = [float(origin_offsets[i + 1]) - float(origin_offsets[i])
                 - css_advances[i] for i in range(gaps)]
    uniform_drift = origin_drift_pt(origin_offsets, css_advances, separator,
                                    (uniform_css or 0.0) * scale, 0.0)

    letters = [residuals[i] for i in range(gaps) if not separator[i]]
    spaces = [residuals[i] for i in range(gaps) if separator[i]]
    if letters and spaces:
        letter_pt = sum(letters) / len(letters)
        word_pt = sum(spaces) / len(spaces) - letter_pt
        letter_css, word_css = gated_spacing_pair(
            letter_pt, word_pt, gaps, separator_gaps, scale)
        drift = origin_drift_pt(origin_offsets, css_advances, separator,
                                (letter_css or 0.0) * scale,
                                (word_css or 0.0) * scale)
        if drift <= uniform_drift + SPACING_CHOICE_SLACK_PT:
            return RunSpacing(letter_css, word_css, gaps, separator_gaps, True,
                              drift, uniform_drift, scale)

    # Separating would move some glyph further from its PDF origin. The cause in
    # every corpus instance is a run that opens with indentation spaces and then
    # justifies: CSS applies word-spacing to every separator in the box, so
    # widening the interior gaps widens the indent by the same amount and no pair
    # fits both. Keep the model that measures better; the plan reports the cost.
    return RunSpacing(uniform_css, None, gaps, separator_gaps, False,
                      uniform_drift, uniform_drift, scale)


def unmodelled_separator_codepoints(text: str) -> list[int]:
    """CSS word separators in `text` that WORD_SEPARATORS does not model."""
    return sorted({ord(char) for char in text} & UNMODELLED_WORD_SEPARATORS)


# ---------------------------------------------------------------------------
# The metric proof
# ---------------------------------------------------------------------------


def precision_bound_pt(size_pt: float) -> float:
    """Largest advance delta that is pure measurement precision, not difference."""
    return IR_QUANTISATION_PT + PDF_WIDTH_HALF_PER_MILLE * size_pt


class GlyphSample:
    """One glyph advance, ours against the PDF's."""

    __slots__ = ("delta", "char", "size_pt", "expected", "actual", "run_text", "per_mille_exact")

    def __init__(self, char: str, size_pt: float, expected: float, actual: float,
                 run_text: str) -> None:
        self.char = char
        self.size_pt = size_pt
        self.expected = expected
        self.actual = actual
        self.delta = abs(expected - actual)
        self.run_text = run_text
        # Round our advance the way a PDF width table must, then quantise the
        # way the IR does. If that reproduces the PDF's number exactly, the two
        # faces are identical to the last digit the PDF can carry.
        per_mille = round(expected / size_pt * 1000.0) if size_pt else 0.0
        self.per_mille_exact = bool(
            abs(round(per_mille / 1000.0 * size_pt, 2) - actual) < 1e-9)

    @property
    def excess(self) -> float:
        return self.delta - precision_bound_pt(self.size_pt)


class FaceEvidence:
    """Accumulates per-glyph and per-run evidence for one resolved face."""

    def __init__(self) -> None:
        self.glyphs: list[GlyphSample] = []
        self.run_natural: list[tuple[float, dict[str, Any], float]] = []
        self.run_residual: list[tuple[float, dict[str, Any]]] = []
        # The spacing model's own share of run_residual: everything except the
        # final glyph's advance-box delta, which is a face-metric fact and is
        # governed by metric_check, not by anything letter-spacing can do.
        self.span_residual: list[tuple[float, dict[str, Any]]] = []
        self.tracking_em: list[float] = []
        # Word spacing is kept apart from `tracking_em` rather than averaged into
        # it: they are different generator operators (Tw against Tc) and mixing
        # them is precisely the error this module stopped making. Only runs that
        # actually separated contribute, so a face with no justified text reports
        # no word spacing instead of reporting zero.
        self.word_spacing_em: list[float] = []
        self.separated_runs = 0
        # (drift, drift-if-uniform, run) for every run, substituted ones
        # included: this is what the emitted CSS costs in glyph origins, and a
        # substitution's contribution to that cost is real even though it says
        # nothing about the generator's spacing.
        self.origin_drift: list[tuple[float, float, dict[str, Any]]] = []
        self.runs = 0
        # Glyphs the face could not draw as written, kept strictly out of
        # `self.glyphs`: they carry no advance measurement of this face, only a
        # count of how often it was asked for something it does not have.
        self.absent: dict[int, AbsentGlyphTally] = {}
        # Runs whose derived tracking is contaminated by a substitution (see
        # `_tracking_record`), held aside so the face's tracking figures stay a
        # statement about the generator's Tc/TJ and nothing else.
        self.substituted_runs = 0

    def absent_counts(self) -> tuple[int, int]:
        """(substituted, unrepresentable) glyph occurrences for this face."""
        substituted = sum(t.count for t in self.absent.values()
                          if t.status == SUBSTITUTED)
        unrepresentable = sum(t.count for t in self.absent.values()
                              if t.status == UNREPRESENTABLE)
        return substituted, unrepresentable

    def coverage_record(self) -> dict[str, Any]:
        """The absence verdict: what this face was asked for and does not have.

        Deliberately a sibling of the advance statistics rather than a term in
        them. A face that is a perfect metric clone of Arial for every glyph it
        contains is still missing U+25CF, and the plan has to be able to say
        both of those things at once without either one contaminating the other.
        """
        substituted, unrepresentable = self.absent_counts()
        return {
            "basis": "codepoints the shipped face has no glyph for. Excluded "
                     "from the advance statistics above: a .notdef width "
                     "measures the placeholder, not the character.",
            "representable_samples": len(self.glyphs),
            "substituted_samples": substituted,
            "unrepresentable_samples": unrepresentable,
            "absent_codepoints": absent_records(self.absent),
        }

    def summarise_glyphs(self) -> dict[str, Any]:
        if not self.glyphs:
            return {"samples": 0, "max_advance_delta_pt": None,
                    "mean_advance_delta_pt": None, "worst": None}
        worst = max(self.glyphs, key=lambda s: (s.delta, s.char, s.size_pt))
        worst_excess = max(self.glyphs, key=lambda s: (s.excess, s.char, s.size_pt))
        total = sum(sample.delta for sample in self.glyphs)
        exact = sum(1 for sample in self.glyphs if sample.per_mille_exact)
        return {
            "basis": "per-glyph advance: CSS face vs the PDF font's own advance "
                     "(IR char_widths_pt); free of Tc/TJ tracking, and over the "
                     "glyphs this face actually contains -- absent codepoints "
                     "are counted in glyph_coverage, never measured here",
            "samples": len(self.glyphs),
            "max_advance_delta_pt": r4(worst.delta),
            "mean_advance_delta_pt": r4(total / len(self.glyphs)),
            "max_advance_delta_em": r4(max(s.delta / s.size_pt for s in self.glyphs
                                           if s.size_pt)),
            "precision_bound_pt_at_worst": r4(precision_bound_pt(worst.size_pt)),
            "max_excess_over_precision_pt": r4(worst_excess.excess),
            "within_measurement_precision": bool(worst_excess.excess <= 1e-9),
            "per_mille_exact_samples": exact,
            "per_mille_exact_fraction": r4(exact / len(self.glyphs)),
            # How much wider or narrower our face runs, glyph for glyph. 1.0 is
            # a clone; the number is the honest size of a substitution error.
            "mean_width_ratio": r4(
                sum(s.expected / s.actual for s in self.glyphs if s.actual)
                / max(1, sum(1 for s in self.glyphs if s.actual))),
            "worst": {
                "text": worst.char,
                "size_pt": r4(worst.size_pt),
                "expected_pt": r4(worst.expected),
                "actual_pt": r4(worst.actual),
                "delta_pt": r4(worst.delta),
                "in_run": worst.run_text,
            },
        }


def evaluate(ir: dict[str, Any], library: FaceLibrary,
             overrides: dict[str, float] | None = None,
             scale_provenance: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the whole font plan: face resolution, metric proof, per-run CSS."""
    overrides = overrides or {}
    if scale_provenance is None:
        _, scale_provenance = scale_overrides(False)
    warnings: list[str] = []
    evidence: dict[str, FaceEvidence] = collections.defaultdict(FaceEvidence)
    resolved: dict[tuple[str, bool, bool], MetricFace | None] = {}
    packages: dict[tuple[str, bool, bool], str | None] = {}
    scales: dict[tuple[str, bool, bool], float] = {}
    run_entries: list[dict[str, Any]] = []
    # Corpus-level absence tally, across every run whether or not its family
    # resolved. A form's U+25CF problem is the same problem whether the run
    # calls the face Arial (resolved, substituted here) or Calibri (no plan at
    # all yet), and a report that only counted the resolved half would
    # understate it by a factor of five.
    absent_corpus: dict[int, AbsentGlyphTally] = {}
    absent_in_unresolved: collections.Counter[int] = collections.Counter()

    def family_scale(family: str) -> float:
        plan = plan_for(family)
        return overrides.get(family, plan.horizontal_scale if plan else 1.0)

    triples = used_faces(ir)
    for family, bold, italic in triples:
        plan = plan_for(family)
        if plan is None:
            resolved[(family, bold, italic)] = None
            packages[(family, bold, italic)] = None
            warnings.append(
                f"UNRESOLVED face {family!r} (weight {css_weight(bold)}, "
                f"{'italic' if italic else 'normal'}): no mapping is defined and no "
                f"substitute may be invented; add a pinned licence-clean face or "
                f"prove no visible text uses it")
            continue
        if plan.package is None:
            resolved[(family, bold, italic)] = None
            packages[(family, bold, italic)] = None
            warnings.append(f"UNRESOLVED face {family!r}: {plan.reason}")
            continue
        style = "italic" if italic else "normal"
        face = library.load(plan.package, css_weight(bold), style)
        packages[(family, bold, italic)] = plan.package
        resolved[(family, bold, italic)] = face
        scales[(family, bold, italic)] = family_scale(family)
        if face is None:
            # A mapping that exists but cannot be loaded is still UNRESOLVED, not
            # a silent fallback: the run below is emitted with no CSS at all
            # rather than with a family whose file we do not ship.
            detail = library.error or "the shipped face could not be read"
            warnings.append(
                f"UNRESOLVED face {family!r} (weight {css_weight(bold)}, {style}): "
                f"it maps to {plan.package} but {detail}. Until that is fixed this "
                f"text has no resolved face; no other family may be substituted.")

    # -- walk every run: metric samples, tracking, CSS ------------------------
    for page in ir["pages"]:
        for index, run in enumerate(page["text_runs"]):
            key = (run["family"], bool(run["bold"]), bool(run["italic"]))
            weight = css_weight(run["bold"])
            face = resolved.get(key)
            plan = plan_for(run["family"])
            package = packages.get(key)
            size = float(run["size_pt"])
            text = run["text"]
            fk = face_key(run["family"], run["bold"], run["italic"])
            entry: dict[str, Any] = {
                "run_index": index,
                "page": page["index"],
                "face_key": fk,
                "text_preview": text[:48],
                "chars": len(text),
            }

            if face is None:
                # No face means no coverage question can be answered, but the
                # characters are still there and the pinned table already knows
                # which of them nothing bundled can draw. Counting them here is
                # what keeps Calibri's 405 bullets in the corpus report instead
                # of vanishing with the family that has no plan yet -- and it is
                # the only place the Wingdings glyphs are seen at all, since
                # Wingdings has no family plan and never will have one.
                unresolved_substitutions: list[dict[str, Any]] = []
                for position, (char, codepoint) in enumerate(
                        zip(text, run_codepoints(run))):
                    if (codepoint not in GLYPH_SUBSTITUTIONS
                            and codepoint not in UNSTATED_CODEPOINTS):
                        continue
                    absent_in_unresolved[codepoint] += 1
                    resolution = GlyphResolution(
                        char, codepoint, UNREPRESENTABLE, None,
                        GLYPH_SUBSTITUTIONS.get(codepoint))
                    tally_absent(absent_corpus, resolution, size,
                                 run["family"], text,
                                 status=table_status(codepoint))
                    # Emitted for an unresolved face too, with the PDF's own
                    # advance on both sides: there is no CSS face to be narrower
                    # than, so the correction is zero and the record exists to
                    # give emit.py the position and the verdict. Without it the
                    # only trace of these seven glyphs would be a corpus count.
                    width = float(run["char_widths_pt"][position]) \
                        if position < len(run["char_widths_pt"]) else 0.0
                    unresolved_substitutions.append(_substitution_record(
                        resolution, position, size, width, width))
                entry["css"] = None
                entry["unresolved"] = True
                # Same three keys as a resolved run, so a consumer branches on a
                # key's value and never on its existence.
                entry["substitutions"] = unresolved_substitutions
                entry["has_substitution"] = False
                entry["has_unrepresentable_glyph"] = bool(unresolved_substitutions)
                run_entries.append(entry)
                continue

            store = evidence[fk]
            store.runs += 1

            # Every advance below is in page space, i.e. already through the
            # scaleX. That keeps `natural`, the tracking and the residual
            # directly comparable to the IR's pt measurements; only the authored
            # CSS values have to be divided back out.
            scale = scales.get(key, 1.0)
            natural = 0.0
            # Per-character page-space advances, kept rather than only summed:
            # derive_spacing needs them gap by gap to tell Tw from Tc.
            css_advances: list[float] = []
            substitutions: list[dict[str, Any]] = []
            unrepresentable_here = 0
            for position, (char, pdf_width, codepoint) in enumerate(
                    zip(text, run["char_widths_pt"], run_codepoints(run))):
                resolution = resolve_glyph(face, codepoint, char)
                css_width = resolution_advance_pt(
                    resolution, face, size, weight, float(pdf_width)) * scale
                natural += css_width
                css_advances.append(css_width)
                if not resolution.absent:
                    store.glyphs.append(
                        GlyphSample(char, size, css_width, float(pdf_width), text[:32]))
                    continue
                tally_absent(store.absent, resolution, size, run["family"], text)
                tally_absent(absent_corpus, resolution, size, run["family"], text,
                             status=table_status(resolution.codepoint))
                substitutions.append(_substitution_record(
                    resolution, position, size, css_width, float(pdf_width)))
                if resolution.status == UNREPRESENTABLE:
                    unrepresentable_here += 1

            measured = float(run["measured_advance_pt"])
            substituted_here = len(substitutions) - unrepresentable_here
            if substituted_here:
                # The gap between this run's natural width and the PDF's is now
                # mostly the stand-in glyph being narrower, not the generator's
                # tracking. Letting it into the face's tracking figures would
                # report a +0.25em "Tc" that no Tc operator produced.
                store.substituted_runs += 1
            else:
                store.run_natural.append((abs(natural - measured), run, natural))

            # Tracking: what the generator added on top of the face's own
            # advances. It used both Tc and Tw, so it takes both CSS properties
            # to reproduce -- see derive_spacing for why one number is not enough
            # and how the choice between the models is made and priced.
            spacing = derive_spacing(text, css_advances,
                                     run.get("char_origin_offsets_pt") or [],
                                     natural, measured, scale)
            if not spacing.measurable and len(text) > 1:
                warnings.append(
                    f"page {page['index']} run {index}: no char_origin_offsets_pt, "
                    f"so letter spacing and word spacing could not be separated "
                    f"and this run keeps one smeared number. Re-extract the IR; "
                    f"summing char_advances_pt instead would report up to 0.65pt "
                    f"of 2dp quantisation as tracking.")
            emitted = spacing.letter_css_pt
            letter_pt = spacing.letter_pt
            applied = spacing.applied_pt

            # width_residual_pt splits cleanly into two terms with different
            # owners, and separating them is what stops one hiding inside the
            # other. The spacing model owns the glyph-origin span; the final
            # glyph's advance box is a face-metric fact that metric_check already
            # judges. The old single-number model made the total come out exact by
            # rolling the second term into the first -- which is to say, by
            # displacing every glyph in the run to make its right-hand edge land.
            offsets = run.get("char_origin_offsets_pt") or []
            origin_span_pt = float(offsets[-1]) if spacing.measurable else None
            span_residual = (sum(css_advances[:-1]) + applied - origin_span_pt
                             if origin_span_pt is not None else None)
            final_glyph_residual = (
                css_advances[-1] - float(run["char_widths_pt"][-1])
                if css_advances and run["char_widths_pt"] else None)

            if spacing.drift_pt is not None:
                store.origin_drift.append(
                    (spacing.drift_pt, spacing.uniform_drift_pt or 0.0, run))
            if not substituted_here:
                store.run_residual.append((abs(natural + applied - measured), run))
                if span_residual is not None:
                    store.span_residual.append((abs(span_residual), run))
                store.tracking_em.append(letter_pt / size if size else 0.0)
                if spacing.separated:
                    store.separated_runs += 1
                    if size and spacing.word_css_pt is not None:
                        store.word_spacing_em.append(spacing.word_pt / size)

            unmodelled = unmodelled_separator_codepoints(text)
            if unmodelled:
                warnings.append(
                    f"page {page['index']} run {index}: contains "
                    + ", ".join(format_codepoint(c) for c in unmodelled)
                    + ", which CSS treats as a word separator but WORD_SEPARATORS "
                      "does not model. Its word gap has been measured as letter "
                      "tracking and smeared across the run's glyphs; add it to "
                      "WORD_SEPARATORS and re-measure.")

            if letter_pt / size <= CONDENSED_TRACKING_EM and size and not substituted_here:
                warnings.append(
                    f"page {page['index']} run {index}: condensed tracking "
                    f"{letter_pt / size:+.4f}em ({format_pt(letter_pt)} at "
                    f"{format_pt(size)}) on {text[:32]!r} -- verify this is real "
                    f"tracking and not a too-wide substitute face")

            css = {
                "font-family": PACKAGES[package].css_stack,
                "font-size": format_pt(size),
                "font-weight": weight,
                "font-style": "italic" if run["italic"] else "normal",
                "letter-spacing": format_pt(emitted) if emitted is not None else None,
                # Only ever present on a run whose residual really is
                # concentrated in its word separators. Omitted rather than set to
                # 0pt otherwise, so that a stylesheet diff shows justification
                # appearing where the generator justified and nowhere else.
                "word-spacing": (format_pt(spacing.word_css_pt)
                                 if spacing.word_css_pt is not None else None),
                "line-height": format_pt(float(run["line_height_pt"])),
                # The advance model above is a plain sum of hmtx advances. Every
                # shipped family carries GPOS `kern` (and some also GSUB `liga`),
                # which Chromium applies by default and which would silently
                # invalidate every number in this plan.
                "font-kerning": "none",
                "font-variant-ligatures": "none",
                "font-feature-settings": "normal",
                # Only meaningful for a face that actually has the axis. Sent to
                # a static face it is inert at best and misleading at worst: it
                # reads as "this weight is interpolated" when the weight in fact
                # comes from a separate file.
                "font-variation-settings": (f'"wght" {weight}'
                                            if face.has_weight_axis else None),
            }
            css.update(transform_css(scale))
            entry["css"] = css
            entry["horizontal_scale"] = scale
            entry["natural_advance_pt"] = r4(natural)
            entry["measured_advance_pt"] = r4(measured)
            # Authored (pre-transform) vs what it measures on paper. Equal
            # whenever scale is 1.0, which is every run of an unscaled family.
            entry["letter_spacing_css_pt"] = emitted
            entry["letter_spacing_pt"] = r4(emitted * scale) if emitted is not None else None
            entry["letter_spacing_em"] = r4(letter_pt / size) if size else None
            entry["word_spacing_css_pt"] = spacing.word_css_pt
            entry["word_spacing_pt"] = (r4(spacing.word_pt)
                                        if spacing.word_css_pt is not None else None)
            entry["word_spacing_em"] = (r4(spacing.word_pt / size)
                                        if size and spacing.word_css_pt is not None
                                        else None)
            entry["spacing_model"] = spacing.model
            entry["word_separator_gaps"] = spacing.separator_gaps
            entry["width_residual_pt"] = r4(natural + applied - measured)
            # width_residual_pt = origin_span_residual_pt + final_glyph_residual_pt
            # (to one 4dp rounding). The first is the spacing model's; the second
            # is the final glyph's own advance against the PDF's box for it, and
            # belongs to metric_check.
            entry["origin_span_residual_pt"] = (r4(span_residual)
                                                if span_residual is not None else None)
            entry["final_glyph_residual_pt"] = (r4(final_glyph_residual)
                                                if final_glyph_residual is not None
                                                else None)
            # The cost of the emitted spacing where it is actually paid. Total
            # width can be exact while an interior glyph is points off its
            # origin, so this is the number to read, and `_if_uniform` is what
            # the single-number model would have cost the same run -- the two
            # together are the proof that separating never made a run worse.
            entry["origin_drift_pt"] = (r4(spacing.drift_pt)
                                        if spacing.drift_pt is not None else None)
            entry["origin_drift_pt_if_uniform"] = (
                r4(spacing.uniform_drift_pt)
                if spacing.uniform_drift_pt is not None else None)
            # Present on every run so a consumer can branch on one key rather
            # than on a key's existence, and so a diff shows a substitution
            # appearing rather than a field appearing.
            entry["substitutions"] = substitutions
            entry["has_substitution"] = bool(substituted_here)
            entry["has_unrepresentable_glyph"] = bool(unrepresentable_here)
            run_entries.append(entry)

    faces_out = [_face_record(family, bold, italic, resolved[(family, bold, italic)],
                             packages[(family, bold, italic)], evidence, library, ir,
                             family_scale(family))
                 for family, bold, italic in triples]

    faces_out.extend(_declared_unused_records(ir, triples, warnings))

    if library.provider is None:
        warnings.append(
            "no shipped face could be read; the metric proof in this plan is "
            "absent, not passing")

    warnings.extend(_absence_warnings(absent_corpus, absent_in_unresolved))

    for record in faces_out:
        check = record.get("metric_check") or {}
        if record["metric_compatible"] and check.get("max_advance_delta_pt") is not None:
            if check["max_advance_delta_pt"] > METRIC_COMPATIBLE_MAX_DELTA_PT:
                warnings.append(
                    f"REFUTED: {record['face_key']} claims metric compatibility but "
                    f"its worst glyph advance is off by "
                    f"{check['max_advance_delta_pt']}pt (limit "
                    f"{METRIC_COMPATIBLE_MAX_DELTA_PT}pt)")

    # Origin drift is a cost, not a failure, and it is pre-existing: it is the
    # part of the generator's per-glyph TJ that no pair of CSS spacing properties
    # can reproduce. One warning per face, naming the worst run, because a
    # cost nobody reads is a cost nobody paid attention to -- and because the
    # audit that scored 137 real defects as clean did so by summarising exactly
    # this kind of number away.
    for record in faces_out:
        drift = ((record.get("tracking_check") or {}).get("origin_drift") or {})
        worst = drift.get("max_origin_drift_pt")
        if worst is None or worst <= ORIGIN_DRIFT_WARN_PT:
            continue
        where = drift.get("worst_origin_drift") or {}
        warnings.append(
            f"ORIGIN DRIFT {record['face_key']}: the emitted spacing leaves a "
            f"glyph {worst}pt from where the PDF put it (tolerance "
            f"{ORIGIN_DRIFT_WARN_PT}pt), worst on {where.get('text')!r} at "
            f"{where.get('size_pt')}pt; {drift.get('runs_over_position_tolerance')} "
            f"of {drift.get('runs')} runs are over it. The single-number model "
            f"would have cost {drift.get('max_origin_drift_pt_if_uniform')}pt on "
            f"the same runs, so this is not a regression -- it is per-glyph TJ "
            f"that neither letter-spacing nor word-spacing can express, and it "
            f"needs the run split at the gaps that disagree.")

    return {
        "schema_version": SCHEMA_VERSION,
        "form": ir["form"],
        "source": {"ir_sha256_of_pdf": ir["source"]["sha256"],
                   "pdf": ir["source"]["file"]},
        "generator": {
            "producer": "tools/formgen/fonts.py",
            "schema_version": SCHEMA_VERSION,
            "metric_source": "fontsource variable WOFF2 hmtx + HVAR "
                             "(no raster, no outlines)",
            "brotli_provider": library.provider,
            "fonts_root": str(library.fonts_root),
            "letter_spacing_model":
                "spacing is derived per gap, not per run: char_advances_pt[i] is "
                "an origin-to-origin distance, so residual[i] = that minus our "
                "face's advance is the generator's Tc/Tw/TJ for that one gap. "
                "Grouping the residuals by whether the gap follows a word "
                "separator gives letter-spacing (the non-separator mean) and "
                "word-spacing (the separator mean minus it); a run with no "
                "separator keeps the single number (measured_advance - CSS "
                "natural advance) / (glyphs - 1) it always had. Blink adds "
                "letter-spacing after the final glyph too, so an inline box "
                "measures one spacing unit wider than measured_advance_pt; every "
                "glyph origin still lands where the PDF put it, which is what "
                "verify.py compares. Do not right-align a spaced run.",
            "word_spacing_model":
                "word-spacing is emitted only when the separated pair leaves no "
                "glyph further from its PDF origin than the single number does "
                "(runs[].origin_drift_pt against origin_drift_pt_if_uniform), so "
                "the split can improve a run and cannot cost one. The case it "
                "cannot fix is a run that opens with indentation spaces and then "
                "justifies: CSS applies word-spacing to every separator in the "
                "box, so no pair fits a +0.07pt indent gap and a +1.02pt word "
                "gap at once. Those runs keep the single number and report their "
                "drift; taking the run origin after the indent is emit.py's.",
            "word_separators": sorted(format_codepoint(c) for c in WORD_SEPARATORS),
            "shaping": "font-kerning and ligatures are disabled in every emitted "
                       "CSS block; both shipped families carry GPOS kern and the "
                       "advance proof is a plain sum of hmtx advances",
            "horizontal_scale": scale_provenance,
            "horizontal_scale_model":
                "a face may carry a scaleX factor: the CSS face supplies the "
                "outlines and the transform supplies the width, which is what "
                "the PDF's Tz operator does. The run element must be "
                "inline-block (transform does not apply to non-replaced inline "
                "boxes) and transform-origin must be the run's left edge, or "
                "every glyph origin in the run shifts. Advances reported here "
                "(natural_advance_pt, letter_spacing_pt, width_residual_pt) are "
                "post-transform page space; css['letter-spacing'] is the "
                "authored pre-transform value, already divided by the scale "
                "because letter-spacing inside a scaled box is scaled too.",
        },
        "faces": faces_out,
        "glyph_substitutions": _glyph_substitution_block(absent_corpus,
                                                         absent_in_unresolved),
        "runs": run_entries,
        "warnings": sorted(set(warnings)),
    }


def _absence_warnings(absent: dict[int, AbsentGlyphTally],
                      in_unresolved: collections.Counter[int]) -> list[str]:
    """One warning per absent codepoint -- never one per occurrence.

    A per-run warning would bury the plan under 405 identical lines for one
    missing bullet and make the substituted and the unrepresentable cases read
    as the same severity. They are not: a substitution is a stated, measured
    compromise, and an unrepresentable glyph is a hole in the page.
    """
    out: list[str] = []
    for codepoint in sorted(absent):
        tally = absent[codepoint]
        where = ", ".join(f"{family} x{count}"
                          for family, count in sorted(tally.families.items()))
        unresolved = in_unresolved.get(codepoint, 0)
        caveat = (f" ({unresolved} of them in runs whose family has no plan yet, "
                  f"so nothing is drawn for those at all)" if unresolved else "")
        if tally.status == SUBSTITUTED and tally.substitution is not None:
            out.append(
                f"SUBSTITUTED {format_codepoint(codepoint)} "
                f"{tally.substitution.name} x{tally.count} [{where}]{caveat}: no "
                f"bundled face contains it; drawn as "
                f"{format_codepoint(tally.substitution.replacement or 0)} "
                f"{tally.substitution.replacement_name}. This is a shape "
                f"compromise, not a metric one -- it is excluded from the "
                f"advance proof and reported in glyph_substitutions.")
        else:
            name = (tally.substitution.name if tally.substitution
                    else ABSENT_GLYPH_NAMES.get(codepoint, "unknown glyph"))
            out.append(
                f"UNREPRESENTABLE {format_codepoint(codepoint)} {name} "
                f"x{tally.count} [{where}]{caveat}: no bundled face contains it "
                f"and no faithful substitute exists, so nothing may be invented "
                f"for it. The PDF's own advance is reserved so the rest of each "
                f"run still lands correctly; what draws there is unresolved. "
                f"Bundle a face that carries the glyph, or render it as artwork.")
    return out


def _substitution_record(resolution: GlyphResolution, position: int, size_pt: float,
                         css_advance_pt: float, pdf_advance_pt: float) -> dict[str, Any]:
    """One character emit.py must draw as something other than what it says.

    Carries the position so the replacement can be applied without re-deriving
    it, and both advances so the caller can decide what to do about the width:
    leave it (the run's letter-spacing already absorbs it and every following
    glyph lands at its PDF origin), or pin the glyph's advance and correct the
    spacing back out. `advance_correction_pt` is what the stand-in is short by.
    """
    substitution = resolution.substitution
    return {
        "index": position,
        "from": format_codepoint(resolution.codepoint),
        "from_char": resolution.char,
        "from_name": substitution.name if substitution else None,
        "status": resolution.status,
        "to": (format_codepoint(resolution.drawn_codepoint)
               if resolution.drawn_codepoint is not None else None),
        "to_char": (chr(resolution.drawn_codepoint)
                    if resolution.drawn_codepoint is not None else None),
        "size_pt": r4(size_pt),
        "css_advance_pt": r4(css_advance_pt),
        "pdf_advance_pt": r4(pdf_advance_pt),
        "advance_correction_pt": r4(pdf_advance_pt - css_advance_pt),
    }


def _glyph_substitution_block(absent: dict[int, AbsentGlyphTally],
                              in_unresolved: collections.Counter[int]) -> dict[str, Any]:
    """The plan's per-character substitution report, for emit.py and for humans.

    `table` is the whole pinned table, including entries this form does not
    reach, so a reader can see what the policy is rather than only what it did
    here. `used` is what this form actually hit, with counts.
    """
    used: list[dict[str, Any]] = []
    for codepoint in sorted(absent):
        record = absent[codepoint].as_record()
        record["occurrences_in_unresolved_faces"] = in_unresolved.get(codepoint, 0)
        used.append(record)
    substituted = sum(t.count for t in absent.values() if t.status == SUBSTITUTED)
    unrepresentable = sum(t.count for t in absent.values()
                          if t.status == UNREPRESENTABLE)
    return {
        "basis": "codepoints no bundled subset (arimo, tinos, roboto-condensed; "
                 "latin and latin-ext) contains. A face that lacks a codepoint "
                 "still answers with gid 0's advance, so coverage is settled "
                 "before any measurement and absent glyphs never enter the "
                 "advance proof.",
        "symbol_encoded_families": sorted(SYMBOL_ENCODED_FAMILIES),
        "symbol_pua_base": format_codepoint(SYMBOL_PUA_BASE),
        "table": [substitution.as_record(codepoint)
                  for codepoint, substitution in sorted(GLYPH_SUBSTITUTIONS.items())],
        "used": used,
        "substituted_occurrences": substituted,
        "unrepresentable_occurrences": unrepresentable,
        "note": "a substituted glyph is drawn as `to` at the run's own "
                "font-size; the run's letter-spacing already absorbs the "
                "advance difference, so every following glyph origin is "
                "unchanged. An unrepresentable glyph has the PDF's own advance "
                "reserved for it and nothing decided about what draws it.",
    }


def _face_record(family: str, bold: bool, italic: bool, face: MetricFace | None,
                 package: str | None, evidence: dict[str, FaceEvidence],
                 library: FaceLibrary, ir: dict[str, Any],
                 horizontal_scale: float = 1.0) -> dict[str, Any]:
    plan = plan_for(family)
    fk = face_key(family, bold, italic)
    store = evidence.get(fk, FaceEvidence())
    basefonts = sorted({name for name, info in ir["fonts"].items()
                        if info["family"] == family
                        and bool(info["declared_bold"]) == bold
                        and bool(info["declared_italic"]) == italic})
    record: dict[str, Any] = {
        "face_key": fk,
        "basefont": basefonts,
        "family": family,
        "bold": bold,
        "italic": italic,
        "status": "resolved" if face is not None else "unresolved",
        # A face with no file must not advertise a CSS family: a consumer that
        # wrote it out would name a font the bundle does not ship, and Chromium
        # would answer with a platform face nothing here has measured.
        "css_family": PACKAGES[package].css_family if package and face else None,
        "css_family_stack": PACKAGES[package].css_stack if package and face else None,
        "css_weight": css_weight(bold),
        "css_style": "italic" if italic else "normal",
        # Which spelling of the package answered is a fact about this checkout,
        # so it is read off the file that loaded rather than assumed.
        "fontsource_package": library.npm_package(face) if face is not None else None,
        "required_packages": _required_packages(package, bold, italic),
        "font_file": library.relative_path(face) if face is not None else None,
        "embedded": _embedded_flag(ir, basefonts),
        # A statement about the face this plan *emits*, so a face with no file
        # can never carry it: the mapping's claim is worth nothing until there
        # is a font to attach it to and per-glyph evidence beside it.
        "metric_compatible": bool(plan and plan.metric_compatible and face is not None),
        "substitution_reason": (plan.reason.format(scale=horizontal_scale) if plan
                               else "no mapping defined for this family"),
        "horizontal_scale": horizontal_scale,
        "css_transform": transform_css(horizontal_scale),
        "used_runs": store.runs,
        "used_glyphs": len(store.glyphs),
    }
    if face is None:
        record["font_face"] = None
        record["metric_check"] = {"samples": 0, "max_advance_delta_pt": None,
                                  "mean_advance_delta_pt": None, "worst": None}
        record["glyph_coverage"] = store.coverage_record()
        record["tracking_check"] = None
        record["vertical_metrics"] = None
        return record

    record["font_face"] = _font_face_record(record, face, library)
    record["metric_check"] = store.summarise_glyphs()
    record["glyph_coverage"] = store.coverage_record()
    record["tracking_check"] = _tracking_record(store)
    record["vertical_metrics"] = _vertical_record(ir, family, face)
    record["shaping_features"] = {
        "gpos_kern": face.has_kern,
        "gsub_liga": face.has_liga,
        "note": "disabled in the emitted CSS; the advance proof is a plain sum "
                "of hmtx advances and shaping would invalidate it",
    }
    return record


def _required_packages(package: str | None, bold: bool, italic: bool) -> list[str]:
    """npm packages that could serve this face, whether or not one is installed.

    Emitted for unresolved faces too -- that is where it earns its keep, because
    it turns "UNRESOLVED" into an instruction. Both fontsource spellings are
    listed for the same reason `ShippedPackage.candidates` offers both.
    """
    if package is None:
        return []
    spec = PACKAGES[package]
    return sorted({npm_package_of(candidate)
                   for candidate in spec.candidates(
                       css_weight(bold), "italic" if italic else "normal",
                       FONTSOURCE_SUBSETS[0])})


def _font_face_record(record: dict[str, Any], face: MetricFace,
                      library: FaceLibrary) -> dict[str, Any]:
    """The exact `@font-face` descriptors this file may be declared with.

    The weight descriptor is the part that has to be derived rather than fixed.
    A variable file legitimately claims the whole `100 900` range; a static file
    must claim only the weight it draws, or the browser picks the one file it has
    for every weight and synthesises the rest -- emboldening outlines and
    inventing advances that no measurement in this plan covers.
    """
    return {
        "family": record["css_family"],
        "style": record["css_style"],
        "weight": "100 900" if face.has_weight_axis else str(record["css_weight"]),
        "file": library.relative_path(face),
        "variable": face.has_weight_axis,
    }


def _embedded_flag(ir: dict[str, Any], basefonts: Sequence[str]) -> bool | None:
    flags = {bool(ir["fonts"][name]["embedded"]) for name in basefonts}
    if len(flags) == 1:
        return flags.pop()
    return None


def _origin_drift_record(store: FaceEvidence) -> dict[str, Any]:
    """What the emitted spacing costs this face in glyph origins.

    Reported over *every* run, substituted ones included, because this is not a
    statement about the generator's operators -- it is the error a reader will
    measure on the page. `_if_uniform` is the same quantity under the
    single-number model this module used to emit, so the pair is the standing
    proof that separating letter from word spacing never moved a glyph further
    from where the PDF put it.

    A drift above ORIGIN_DRIFT_WARN_PT is a run whose gaps genuinely differ from
    each other -- per-glyph TJ, or a justified line that also carries
    indentation. No (letter-spacing, word-spacing) pair can reproduce one, so the
    honest report is the size of the miss, not a smaller-looking summary.
    """
    if not store.origin_drift:
        return {"runs": 0}
    worst = max(store.origin_drift, key=lambda item: item[0])
    return {
        "basis": "worst accumulated glyph-origin error left by the emitted "
                 "letter-spacing/word-spacing pair, per run. Stricter than "
                 "width_residual_pt, which only checks the total.",
        "runs": len(store.origin_drift),
        "max_origin_drift_pt": r4(worst[0]),
        "mean_origin_drift_pt": r4(sum(item[0] for item in store.origin_drift)
                                   / len(store.origin_drift)),
        "max_origin_drift_pt_if_uniform": r4(
            max(item[1] for item in store.origin_drift)),
        "mean_origin_drift_pt_if_uniform": r4(
            sum(item[1] for item in store.origin_drift) / len(store.origin_drift)),
        "runs_over_position_tolerance": sum(
            1 for item in store.origin_drift if item[0] > ORIGIN_DRIFT_WARN_PT),
        "position_tolerance_pt": ORIGIN_DRIFT_WARN_PT,
        "worst_origin_drift": {
            "text": worst[2]["text"][:48],
            "size_pt": r4(worst[2]["size_pt"]),
            "drift_pt": r4(worst[0]),
            "drift_pt_if_uniform": r4(worst[1]),
        },
    }


def _tracking_record(store: FaceEvidence) -> dict[str, Any]:
    """Per-face tracking evidence, over runs whose width model is uncontaminated.

    Runs containing a substituted glyph are excluded and counted separately.
    Their (measured - natural) gap is dominated by the stand-in being narrower
    than the character it replaces -- 1.28pt on a 5.04pt bullet -- and averaging
    that into the face's tracking would report a quarter-em of `Tc` that no Tc
    operator ever emitted. The affected runs still get their own letter-spacing
    in `runs`; what is excluded is only their contribution to this summary.

    Word spacing is reported beside letter spacing and never inside it: they are
    two different generator operators, and averaging a justified line's Tw into
    its Tc is the defect this model was written to stop making. Origin drift is
    reported for every run whether or not it separated -- see
    `_origin_drift_record`.
    """
    drift = _origin_drift_record(store)
    if not store.run_natural:
        return {"runs": 0,
                "runs_excluded_for_substitution": store.substituted_runs,
                "origin_drift": drift}
    worst_natural = max(store.run_natural, key=lambda item: item[0])
    worst_residual = max(store.run_residual, key=lambda item: item[0])
    return {
        "runs_word_spaced": store.separated_runs,
        "word_spacing_em": ({
            "min": r4(min(store.word_spacing_em)),
            "max": r4(max(store.word_spacing_em)),
            "mean": r4(sum(store.word_spacing_em) / len(store.word_spacing_em)),
        } if store.word_spacing_em else None),
        "origin_drift": drift,
        "basis": "run advance: CSS face natural width vs IR measured_advance_pt; "
                 "the gap is the generator's Tc/Tw/TJ tracking, not a face "
                 "mismatch. letter_spacing_em is the Tc half only -- the Tw half "
                 "is word_spacing_em, over the runs_word_spaced runs whose "
                 "residual proved to be concentrated in their word separators. "
                 "Runs containing a substituted glyph are excluded and counted "
                 "in runs_excluded_for_substitution.",
        "runs": len(store.run_natural),
        "runs_excluded_for_substitution": store.substituted_runs,
        "max_untracked_delta_pt": r4(worst_natural[0]),
        "mean_untracked_delta_pt": r4(
            sum(item[0] for item in store.run_natural) / len(store.run_natural)),
        "worst_untracked": {
            "text": worst_natural[1]["text"][:48],
            "size_pt": r4(worst_natural[1]["size_pt"]),
            "expected_pt": r4(worst_natural[2]),
            "actual_pt": r4(worst_natural[1]["measured_advance_pt"]),
        },
        "letter_spacing_em": {
            "min": r4(min(store.tracking_em)),
            "max": r4(max(store.tracking_em)),
            "mean": r4(sum(store.tracking_em) / len(store.tracking_em)),
        },
        "max_residual_after_letter_spacing_pt": r4(worst_residual[0]),
        "worst_residual_text": worst_residual[1]["text"][:48],
        # The spacing model's own share of the residual above. This is the number
        # that has to be near zero: the rest of max_residual_after_letter_spacing
        # is the final glyph's advance-box delta, which metric_check owns and
        # which no spacing property should be used to absorb.
        "max_origin_span_residual_pt": (r4(max(item[0] for item in store.span_residual))
                                        if store.span_residual else None),
    }


def _vertical_record(ir: dict[str, Any], family: str, face: MetricFace) -> dict[str, Any]:
    """Line-box metrics: the 'custom spacing' half of the brief.

    line-height is emitted in absolute pt, so the box height is face-independent
    by construction. The ascender/descender comparison still matters because it
    decides where the baseline sits inside that box (half-leading).
    """
    samples = {(r["ascender"], r["descender"])
               for page in ir["pages"] for r in page["text_runs"]
               if r["family"] == family}
    pdf_ascender, pdf_descender = sorted(samples)[0] if samples else (None, None)
    record = {
        "pdf_ascender": pdf_ascender,
        "pdf_descender": pdf_descender,
        "css_hhea_ascender": r4(face.hhea_ascender),
        "css_hhea_descender": r4(face.hhea_descender),
        "css_hhea_line_gap": r4(face.hhea_line_gap),
        "line_height_emitted": "absolute pt per run (IR line_height_pt); never "
                               "left to the browser default, which is face-derived",
    }
    if pdf_ascender is not None:
        record["ascender_delta"] = r4(face.hhea_ascender - pdf_ascender)
        record["descender_delta"] = r4(face.hhea_descender - pdf_descender)
        record["baseline_matches"] = bool(
            abs(record["ascender_delta"]) <= 0.005
            and abs(record["descender_delta"]) <= 0.005)
    return record


def _declared_unused_records(ir: dict[str, Any],
                             triples: Sequence[tuple[str, bool, bool]],
                             warnings: list[str]) -> list[dict[str, Any]]:
    """Families present in the PDF resources that no visible run uses.

    2551Q declares Times New Roman and only ever sets it for whitespace-only
    footer artifacts, which extract.py drops. That is not a resolved face and it
    is not a failure either -- it is a fact that has to be stated, because the
    moment a revision puts real text in it the plan must fail instead of guess.
    """
    used = {family for family, _b, _i in triples}
    out: list[dict[str, Any]] = []
    for name, info in sorted(ir["fonts"].items()):
        family = info["family"]
        if family in used:
            continue
        plan = plan_for(family)
        out.append({
            "face_key": f"{family}|declared-unused",
            "basefont": [name],
            "family": family,
            "bold": bool(info["declared_bold"]),
            "italic": bool(info["declared_italic"]),
            "status": "declared-unused",
            "css_family": None,
            "css_family_stack": None,
            "css_weight": css_weight(bool(info["declared_bold"])),
            "css_style": "italic" if info["declared_italic"] else "normal",
            "fontsource_package": None,
            "required_packages": _required_packages(
                plan.package if plan else None, bool(info["declared_bold"]),
                bool(info["declared_italic"])),
            "font_file": None,
            "font_face": None,
            "embedded": bool(info["embedded"]),
            "metric_compatible": False,
            "substitution_reason": (plan.reason.format(scale=plan.horizontal_scale)
                                    if plan else
                                    "no mapping defined for this family")
                                   + " Declared in the page resources but used by "
                                     "no visible text run in this revision.",
            "used_runs": 0,
            "used_glyphs": 0,
            "metric_check": {"samples": 0, "max_advance_delta_pt": None,
                             "mean_advance_delta_pt": None, "worst": None},
            # Same shape as a used face, all zeros: nothing was set in it, so
            # nothing could be missing from it. A null here would read as "not
            # checked", which is a different claim.
            "glyph_coverage": FaceEvidence().coverage_record(),
            "tracking_check": None,
            "vertical_metrics": None,
        })
        warnings.append(
            f"{family!r} is declared in the PDF font resources but no visible text "
            f"run uses it; it is deliberately left UNRESOLVED. If a later revision "
            f"sets visible text in it, this plan must fail rather than substitute.")
    return out


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def print_face_table(plan: dict[str, Any], stream: Any = sys.stderr) -> None:
    header = (f"{'face':34} {'css face':22} {'scaleX':>8} {'runs':>5} {'glyphs':>7} "
              f"{'max Δpt':>9} {'mean Δpt':>9}  verdict")
    print(header, file=stream)
    print("-" * len(header), file=stream)
    for face in plan["faces"]:
        check = face["metric_check"]
        maximum = check["max_advance_delta_pt"]
        mean = check["mean_advance_delta_pt"]
        if face["status"] != "resolved":
            verdict = face["status"].upper()
        elif not face["metric_compatible"]:
            verdict = "SUBSTITUTE (not metric-compatible)"
        elif check["within_measurement_precision"]:
            verdict = (f"IDENTICAL ({check['per_mille_exact_fraction']:.0%} of glyphs "
                       f"exact at PDF per-mille precision)")
        elif maximum is not None and maximum <= METRIC_COMPATIBLE_MAX_DELTA_PT:
            verdict = "compatible"
        else:
            verdict = "REFUTED"
        scale = face.get("horizontal_scale", 1.0)
        print(f"{face['face_key']:34} {str(face['css_family'] or '-'):22} "
              f"{'-' if scale == 1.0 else format_scale(scale):>8} "
              f"{face['used_runs']:5} {face['used_glyphs']:7} "
              f"{'-' if maximum is None else f'{maximum:9.4f}'} "
              f"{'-' if mean is None else f'{mean:9.4f}'}  {verdict}", file=stream)
        # Absence gets its own line under the face it belongs to, never a column
        # in the verdict above: the verdict describes advances, and a glyph the
        # face does not have has no advance to describe.
        for absent in (face.get("glyph_coverage") or {}).get("absent_codepoints", []):
            drawn = (f"drawn as {absent['replacement']} {absent['replacement_char']}"
                     if absent["status"] == SUBSTITUTED else "NOT DRAWN")
            print(f"{'':34} {absent['status'].upper():>22} {absent['codepoint']} "
                  f"{absent['name']} x{absent['count']} -- {drawn}", file=stream)

    print("", file=stream)
    for face in plan["faces"]:
        track = face.get("tracking_check")
        if not track:
            continue
        spacing = track["letter_spacing_em"]
        if not track.get("runs"):
            continue
        excluded = track.get("runs_excluded_for_substitution") or 0
        print(f"{face['face_key']:34} tracking {spacing['min']:+.4f}..{spacing['max']:+.4f}em "
              f"(mean {spacing['mean']:+.4f}em)  residual after letter-spacing "
              f"{track['max_residual_after_letter_spacing_pt']:.4f}pt"
              + (f"  [{excluded} runs excluded: substituted glyph]" if excluded else ""),
              file=stream)
        # Word spacing and origin drift get their own line under the face rather
        # than more columns on that one: they are separate measurements, and the
        # drift figure has to sit next to what the old model would have cost or
        # it reads as a regression.
        word = track.get("word_spacing_em")
        drift = track.get("origin_drift") or {}
        if word:
            print(f"{'':34} word-spacing {word['min']:+.4f}..{word['max']:+.4f}em "
                  f"(mean {word['mean']:+.4f}em) on {track['runs_word_spaced']} "
                  f"justified runs", file=stream)
        if drift.get("max_origin_drift_pt") is not None:
            print(f"{'':34} origin drift max {drift['max_origin_drift_pt']:.4f}pt "
                  f"(uniform model {drift['max_origin_drift_pt_if_uniform']:.4f}pt), "
                  f"mean {drift['mean_origin_drift_pt']:.4f}pt, "
                  f"{drift['runs_over_position_tolerance']}/{drift['runs']} runs over "
                  f"{drift['position_tolerance_pt']}pt", file=stream)

    used = plan["glyph_substitutions"]["used"]
    if used:
        print("", file=stream)
        print("glyph substitutions", file=stream)
        for entry in used:
            target = (f"-> {entry['replacement']} {entry['replacement_char']}"
                      if entry["status"] == SUBSTITUTED
                      else "-> (none: unrepresentable)")
            print(f"  {entry['codepoint']} {display_char(ord(entry['char'])):8} "
                  f"x{entry['count']:<5} {target:32} {entry['name']}", file=stream)
            print(f"{'':4}families {entry['families']}, sizes {entry['sizes_pt']}pt, "
                  f"{entry['occurrences_in_unresolved_faces']} in unresolved faces",
                  file=stream)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

SELF_TEST_IR = pathlib.Path("build/ir/2551q-2018.ir.json")

# 2551Q sets nothing outside the shipped subsets, so it proves the substitution
# machinery is *inert* where coverage is complete but can never exercise it.
# 1601C is the one sheet that hits every branch at once: 23 U+25CF in a resolved
# Arial face (substituted), 14 more in Calibri (no family plan yet), and both
# spellings of the Wingdings square bullet (unrepresentable, and one of them
# only reachable through the symbol-encoding fold).
SELF_TEST_SUBSTITUTION_IR = pathlib.Path("build/ir/1601c-2018.ir.json")

# What 1601C must report. Pinned as counts rather than "more than zero" so a
# regression that silently stops seeing one of the two spellings, or starts
# double-counting, fails instead of passing quietly.
SELF_TEST_SUBSTITUTED_BLACK_CIRCLES = 37
SELF_TEST_ARIAL_BOLD_BLACK_CIRCLES = 23
SELF_TEST_UNREPRESENTABLE_SQUARES = 2

# The pinned source advances must agree with what the PDFs actually carry. Two
# per-mille of an em is four times the PDF width table's own precision, so this
# catches a mis-transcribed pin without firing on rounding.
SOURCE_ADVANCE_TOLERANCE_EM = 0.002

# The third self-test document set. 2551Q proves the substitution path stays
# inert where coverage is complete and 1601C proves it behaves where it is not
# and the source *states* the codepoint. These two are the only forms in the
# corpus where the source states no codepoint at all, and both are needed: 2550M
# carries four of the seven glyphs at two sizes and 2553 the other three, so
# pinning one alone would let a regression that stops reading the field on one
# page pass on the other. 2553 is additionally the corpus's worst justified text,
# which makes it the document S6 has to be measured on.
SELF_TEST_UNMAPPED_IRS = (pathlib.Path("build/ir/2553-1999.ir.json"),
                          pathlib.Path("build/ir/2550m-2007.ir.json"))

# Wingdings glyph-131 occurrences per form. Counts, not "more than zero", so a
# regression that double-counts or silently loses a page fails instead of
# passing quietly -- the failure mode this project has already been burned by.
SELF_TEST_UNMAPPED_SQUARES = {"2553": 3, "2550M": 4}

# What 2553's justified prose must report once letter and word spacing are
# separated. Bounds with margin rather than exact floats: the measurements move
# with a fontsource release, the facts do not.
#
#   * letter tracking on Times New Roman fell from a max of 0.1221em -- a whole
#     line of 9.84pt prose tracked out by 1.2pt per glyph -- to 0.0614em, which
#     is the sheet's genuinely letterspaced headings and nothing else;
#   * 56 of its 81 Times New Roman runs are justified and now carry word-spacing;
#   * the worst accumulated glyph-origin error on that face fell from 11.5342pt
#     to 7.2862pt, a 36.8% recovery. The remainder is per-glyph TJ that no CSS
#     spacing pair can express, and is reported rather than hidden.
SELF_TEST_MAX_LETTER_SPACING_EM = 0.08
SELF_TEST_MIN_WORD_SPACED_RUNS = 40
SELF_TEST_MIN_DRIFT_RECOVERY = 0.30


def substitution_table_failures(library: FaceLibrary) -> list[str]:
    """Check GLYPH_SUBSTITUTIONS against the fonts it claims to describe.

    The table is a set of claims about the shipped binaries -- "no bundled face
    has this codepoint", "every bundled family has this stand-in" -- and a claim
    that is never put to the font is just a comment. Both halves are re-derived
    here from the files on disk, so an entry cannot outlive the fontsource
    release that justified it.

    The two halves quantify over the subsets differently, and that asymmetry is
    the point. fontsource ships `latin-ext` as the *complement* of `latin`, not
    as a superset, so absence has to hold across every subset while presence
    only has to hold in one of them -- the browser loads both `@font-face`
    declarations and resolves per glyph. Demanding the stand-in in both would
    reject U+2022 for living, correctly, only in `latin`.
    """
    failures: list[str] = []
    for codepoint, substitution in sorted(GLYPH_SUBSTITUTIONS.items()):
        for package in sorted(PACKAGES):
            faces = [(FONTSOURCE_SUBSETS[1] if extended else FONTSOURCE_SUBSETS[0],
                      library.load(package, 400, "normal", extended=extended))
                     for extended in (False, True)]
            faces = [(subset, face) for subset, face in faces if face is not None]
            if not faces:
                continue
            for subset, face in faces:
                if face.has(codepoint):
                    failures.append(
                        f"{format_codepoint(codepoint)} is listed as absent but "
                        f"{package} {subset} contains it; the substitution is "
                        f"unnecessary and would replace a glyph we ship")
            if substitution.replacement is None:
                continue
            if not any(face.has(substitution.replacement) for _subset, face in faces):
                failures.append(
                    f"{format_codepoint(codepoint)} substitutes "
                    f"{format_codepoint(substitution.replacement)}, which no "
                    f"subset of {package} contains either")
                continue
            # The stand-in's advance is the one number in the reason text a
            # running program can check, so it is checked -- but only against
            # the package the affected runs actually resolve to. It is not a
            # constant across families: Arimo draws U+2022 at 0.3501em (Arial's
            # own advance, which is why the stated 58% cost is exact) and Roboto
            # Condensed draws it at 0.3374em. Pinning one number and asserting it
            # everywhere would either be false or force the cost to be stated as
            # a range that describes no real substitution.
            if package != SUBSTITUTION_REFERENCE_PACKAGE:
                continue
            for subset, face in faces:
                if not face.has(substitution.replacement):
                    continue
                observed = (face.advance_units(face.cmap[substitution.replacement],
                                               face.coords(400))
                            / face.units_per_em)
                pinned = substitution.replacement_advance_em or 0.0
                if abs(observed - pinned) > SOURCE_ADVANCE_TOLERANCE_EM:
                    failures.append(
                        f"{format_codepoint(codepoint)}: pinned replacement "
                        f"advance {pinned}em is not what {package} {subset} "
                        f"draws ({observed:.4f}em); the stated cost is wrong")
    return failures


def spacing_invariant_failures(plan: dict[str, Any]) -> list[str]:
    """Run-level invariants the spacing model must hold on any document at all.

    Shared by every self-test half rather than written once per form, because
    these are properties of the model and not of a particular sheet. A half that
    skipped them would be a half in which the model could quietly break.

    The residual check is model-aware, and that is the point rather than a
    convenience: a separated run is answerable for the glyph-origin span, which
    it fits exactly, and a uniform run is answerable for the total measured
    width, which it fits by construction. Neither may be asked to absorb the
    other's error, and the difference between the two is the final glyph's own
    advance box -- a face-metric fact that metric_check judges and that no
    spacing property should be used to hide.
    """
    failures: list[str] = []
    for entry in plan["runs"]:
        if entry.get("css") is None:
            continue
        where = f"page {entry['page']} run {entry['run_index']}"
        css = entry["css"]
        model = entry.get("spacing_model")
        if model not in ("letter+word", "letter-uniform"):
            failures.append(f"{where}: unknown spacing model {model!r}")
        authored = entry.get("word_spacing_css_pt")
        if (css.get("word-spacing") is None) != (authored is None):
            failures.append(
                f"{where}: css word-spacing {css.get('word-spacing')!r} and "
                f"word_spacing_css_pt {authored!r} disagree on presence")
        if authored is not None:
            if model != "letter+word":
                failures.append(
                    f"{where}: word-spacing emitted under the {model} model")
            if r4(authored * entry["horizontal_scale"]) != entry["word_spacing_pt"]:
                failures.append(
                    f"{where}: authored word-spacing {authored}pt does not scale "
                    f"to the effective {entry['word_spacing_pt']}pt")
        # THE licence for separating the two spacings: it may improve a run and
        # may never cost one. Compared at the emitted precision, since both
        # fields are r4'd -- this is rounding, not slack.
        drift = entry.get("origin_drift_pt")
        uniform = entry.get("origin_drift_pt_if_uniform")
        if drift is not None and uniform is not None and drift > uniform + 0.0001:
            failures.append(
                f"{where}: the emitted spacing leaves a glyph {drift}pt from its "
                f"PDF origin where the single-number model would have left it "
                f"{uniform}pt. Separating letter from word spacing must never "
                f"cost geometry.")
        # A single-glyph run has no gap, so no spacing is emitted for it and there
        # is no spacing model to hold to anything: its whole residual is that one
        # glyph's advance against the PDF's box for it, which is metric_check's
        # to judge and, where the glyph was substituted, is the substitution's
        # stated cost. 18 runs in the corpus are a lone U+25CF, and blaming their
        # 1.5194pt bullet shortfall on letter-spacing would be reading the wrong
        # meter.
        if (entry.get("chars") or 0) < 2:
            continue
        residual = (entry.get("origin_span_residual_pt") if model == "letter+word"
                    else entry.get("width_residual_pt"))
        if residual is not None and abs(residual) > LETTER_SPACING_ACCUMULATED_PT:
            failures.append(
                f"{where}: the {model} model misses its own target by "
                f"{r4(residual)}pt (limit {LETTER_SPACING_ACCUMULATED_PT}pt)")
    return failures


def self_test(ir_path: pathlib.Path, fonts_root: pathlib.Path | None) -> int:
    if not ir_path.is_file():
        print(f"self-test needs the real IR at {ir_path}", file=sys.stderr)
        return 2
    ir = json.loads(ir_path.read_text(encoding="utf-8"))
    if fonts_root is None:
        print("self-test needs node_modules/@fontsource-variable", file=sys.stderr)
        return 2

    library = FaceLibrary(fonts_root)
    plan = evaluate(ir, library)
    failures: list[str] = []

    if plan["generator"]["brotli_provider"] is None:
        failures.append("no brotli provider: the metric proof did not run at all")

    # 1. Every used face either resolves or is explicitly warned about.
    for face in plan["faces"]:
        if face["status"] == "resolved":
            continue
        if not any(face["family"] in warning for warning in plan["warnings"]):
            failures.append(f"{face['face_key']} is unresolved with no warning")

    # 2. THE headline assertion. If Arimo's advances ever drift from Arial's,
    #    the whole substitution premise is dead -- for Arial Narrow too, which
    #    is now the same face under a transform. Delta itself is checked in 4;
    #    what is specific to Arial is that the baseline must line up as well.
    arial = [f for f in plan["faces"]
             if f["family"] == "Arial" and f["status"] == "resolved"]
    if len(arial) != 3:
        failures.append(f"expected 3 resolved Arial faces, got {len(arial)}")
    for face in arial:
        vertical = face["vertical_metrics"]
        if not vertical["baseline_matches"]:
            failures.append(
                f"{face['face_key']}: hhea ascender/descender differ from the PDF "
                f"font ({vertical['ascender_delta']}, {vertical['descender_delta']})")

    # 3. Arial Narrow now resolves to scaled Arimo, so it is held to the same
    #    bar as everything else -- and the scale must actually be emitted.
    narrow = [f for f in plan["faces"] if f["family"] == "Arial Narrow"
              and f["status"] == "resolved"]
    if len(narrow) != 2:
        failures.append(f"expected 2 resolved Arial Narrow faces, got {len(narrow)}")
    for face in narrow:
        if face["fontsource_package"] != "@fontsource-variable/arimo":
            failures.append(
                f"{face['face_key']}: Arial Narrow must resolve to Arimo plus a "
                f"scaleX, not to {face['fontsource_package']}")
        if not 0.5 < face["horizontal_scale"] < 1.0:
            failures.append(
                f"{face['face_key']}: horizontal_scale {face['horizontal_scale']} "
                f"is not a condensation")
        transform = face["css_transform"]
        if not transform["transform"] or transform["display"] != "inline-block":
            failures.append(
                f"{face['face_key']}: a scaled face must emit both a scaleX "
                f"transform and display:inline-block, got {transform}")

    # 4. EVERY used face is metric compatible within SELF_TEST_MAX_DELTA_PT.
    #    This is the assertion the Narrow fix bought; it was unmeetable while
    #    Arial Narrow sat at 1.358pt against Roboto Condensed.
    used = [f for f in plan["faces"] if f["status"] == "resolved"]
    if not used:
        failures.append("no resolved faces at all")
    for face in used:
        worst = face["metric_check"]["max_advance_delta_pt"]
        if not face["metric_compatible"]:
            failures.append(
                f"{face['face_key']} is used by {face['used_runs']} runs but is "
                f"not metric compatible")
        if worst is None or worst > SELF_TEST_MAX_DELTA_PT:
            failures.append(
                f"{face['face_key']}: worst glyph advance delta {worst}pt exceeds "
                f"{SELF_TEST_MAX_DELTA_PT}pt. Fix the face mapping -- never widen "
                f"this tolerance.")
        if not face["metric_check"]["within_measurement_precision"]:
            failures.append(
                f"{face['face_key']}: delta {worst}pt exceeds what IR quantisation "
                f"plus the PDF's per-mille width table can explain (excess "
                f"{face['metric_check']['max_excess_over_precision_pt']}pt)")

    # 4b. Whatever a resolved face claims about the wght axis has to match the
    #     file, in both directions: a variable file must be told to vary and a
    #     static one must not, and the @font-face weight descriptor must not
    #     invite the browser to synthesise a weight nothing here measured.
    for face in used:
        font_face = face.get("font_face") or {}
        variable = bool(font_face.get("variable"))
        if font_face.get("weight") != ("100 900" if variable else str(face["css_weight"])):
            failures.append(
                f"{face['face_key']}: @font-face weight {font_face.get('weight')!r} "
                f"does not match a {'variable' if variable else 'static'} file; a "
                f"static face declared over a range makes the browser synthesise "
                f"the weights it has no file for")
        for entry in plan["runs"]:
            if entry["face_key"] != face["face_key"] or not entry.get("css"):
                continue
            varies = entry["css"].get("font-variation-settings") is not None
            if varies != variable:
                failures.append(
                    f"page {entry['page']} run {entry['run_index']}: "
                    f"font-variation-settings is "
                    f"{'set' if varies else 'absent'} for a "
                    f"{'variable' if variable else 'static'} face")
                break

    # 4c. Times New Roman is the corpus's serif and it must never be served by a
    #     sans face. It resolves only to Tinos, and only with the proof attached;
    #     with the package absent it must stay unresolved and say which package
    #     would fix it, rather than quietly borrowing Arimo.
    for face in plan["faces"]:
        if plan_for(face["family"]) is not FAMILY_PLANS.get("Times New Roman"):
            continue
        if face["status"] == "resolved":
            if face["fontsource_package"] not in ("@fontsource/tinos",
                                                  "@fontsource-variable/tinos"):
                failures.append(
                    f"{face['face_key']}: Times New Roman resolved to "
                    f"{face['fontsource_package']}, which is not Tinos")
        elif "tinos" not in " ".join(face["required_packages"]):
            failures.append(
                f"{face['face_key']}: an unresolved Times New Roman face must name "
                f"the package that would resolve it, got {face['required_packages']}")

    # 5. The pinned scale is the default, and it still agrees with the faces.
    if plan["generator"]["horizontal_scale"]["source"] != "pinned-constant":
        failures.append("the default plan must use the pinned scale, not a "
                        "machine-dependent derived one")
    derived = derive_horizontal_scale()
    if derived is not None and abs(derived - ARIAL_NARROW_HORIZONTAL_SCALE) > 1e-4:
        failures.append(
            f"pinned Arial Narrow scale {ARIAL_NARROW_HORIZONTAL_SCALE} disagrees "
            f"with the system faces ({derived}); re-measure before shipping")

    # 6. Every run gets a CSS block, with an explicit line-height, and a scaled
    #    run carries the transform that makes its advances come out right.
    total_runs = sum(len(p["text_runs"]) for p in ir["pages"])
    if len(plan["runs"]) != total_runs:
        failures.append(f"plan covers {len(plan['runs'])} runs, IR has {total_runs}")
    scaled_runs = 0
    for entry in plan["runs"]:
        css = entry["css"]
        where = f"page {entry['page']} run {entry['run_index']}"
        if css is None:
            failures.append(f"{where}: no CSS")
            continue
        for required in ("font-family", "font-size", "font-weight", "font-style",
                         "line-height"):
            if not css.get(required):
                failures.append(f"{where}: missing {required}")
        if entry["horizontal_scale"] != 1.0:
            scaled_runs += 1
            if css["transform"] != f"scaleX({format_scale(entry['horizontal_scale'])})":
                failures.append(f"{where}: scaled run without a matching transform")
            if css["display"] != "inline-block":
                failures.append(
                    f"{where}: scaled run is not inline-block, so the browser "
                    f"would drop the transform entirely")
            if css["transform-origin"] != "0% 0%":
                failures.append(
                    f"{where}: transform-origin {css['transform-origin']!r} is not "
                    f"the run's left edge; every glyph origin would shift")
        elif css["transform"] is not None:
            failures.append(f"{where}: unscaled run must not emit a transform")
        # The authored letter-spacing has to survive the transform as the
        # page-space value the tracking model derived.
        authored, effective = entry["letter_spacing_css_pt"], entry["letter_spacing_pt"]
        if (authored is None) != (effective is None):
            failures.append(f"{where}: letter-spacing pair disagrees on presence")
        elif authored is not None and r4(authored * entry["horizontal_scale"]) != effective:
            # Exact on the rounded value rather than a tolerance: both fields are
            # r4'd, so the relation is reproducible to the last emitted digit.
            failures.append(
                f"{where}: authored letter-spacing {authored}pt does not scale to "
                f"the effective {effective}pt")
    if scaled_runs != 10:
        failures.append(f"expected 10 scaled runs on 2551Q, got {scaled_runs}")

    # 6b. The spacing model's own invariants, on this document like any other.
    failures.extend(spacing_invariant_failures(plan))

    # 7. The pinned substitution table still describes the fonts on disk.
    failures.extend(substitution_table_failures(library))

    # 7b. 2551Q sets nothing outside the shipped subsets, so the substitution
    #     path must be provably inert here: no absent glyph, no substitution,
    #     and -- the point of the fix -- no .notdef width anywhere in the proof.
    #     Its faces already assert 100% per-mille-exact above, which only holds
    #     because no gid 0 is being measured.
    if plan["glyph_substitutions"]["used"]:
        failures.append(
            f"2551Q needs no substitutions but the plan reports "
            f"{plan['glyph_substitutions']['used']}")
    for face in plan["faces"]:
        coverage = face["glyph_coverage"]
        if coverage["absent_codepoints"]:
            failures.append(f"{face['face_key']}: unexpected absent glyphs on "
                            f"2551Q: {coverage['absent_codepoints']}")
    for entry in plan["runs"]:
        if entry.get("substitutions"):
            failures.append(f"page {entry['page']} run {entry['run_index']}: "
                            f"unexpected substitution on 2551Q")
            break

    # 8. Determinism: two evaluations must serialise identically.
    again = json.dumps(evaluate(ir, FaceLibrary(fonts_root)), sort_keys=False)
    if again != json.dumps(plan, sort_keys=False):
        failures.append("output is not deterministic across runs")

    print_face_table(plan, sys.stdout)
    print("", file=sys.stdout)
    print(f"warnings: {len(plan['warnings'])}", file=sys.stdout)
    for warning in plan["warnings"][:6]:
        print(f"  - {warning}", file=sys.stdout)
    if len(plan["warnings"]) > 6:
        print(f"  … {len(plan['warnings']) - 6} more", file=sys.stdout)

    print("", file=sys.stdout)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stdout)
        return 1
    print(f"self-test OK: {len(plan['faces'])} faces, {len(plan['runs'])} runs, "
          f"brotli via {plan['generator']['brotli_provider']}", file=sys.stdout)
    return 0


def pdf_advances_em(ir: dict[str, Any], codepoint: int) -> list[float]:
    """Every advance the PDF itself records for one codepoint, in em.

    The pinned `source_advance_em` figures are the whole basis for saying how
    much a substitution costs, so they are checked against the document rather
    than trusted. Sizes come from the same runs, and the full fold is applied
    here too -- so the Wingdings pin is validated against all three spellings:
    the bare byte, the private-use codepoint, and the glyph id of a run whose
    font states no codepoint at all.
    """
    out: list[float] = []
    for page in ir["pages"]:
        for run in page["text_runs"]:
            size = float(run["size_pt"])
            if not size:
                continue
            for run_codepoint, width in zip(run_codepoints(run),
                                            run["char_widths_pt"]):
                if run_codepoint == codepoint:
                    out.append(float(width) / size)
    return out


def substitution_self_test(ir_path: pathlib.Path,
                           fonts_root: pathlib.Path | None) -> int:
    """Assert the absence and substitution machinery against the real 1601C IR.

    Everything here is a statement about a form that genuinely contains glyphs
    no bundled face has. The 2551Q self-test proves the path stays out of the
    way when coverage is complete; this one proves it does the right thing when
    it is not.
    """
    if not ir_path.is_file():
        print(f"substitution self-test needs the real IR at {ir_path}",
              file=sys.stderr)
        return 2
    if fonts_root is None:
        print("substitution self-test needs node_modules/@fontsource-variable",
              file=sys.stderr)
        return 2
    ir = json.loads(ir_path.read_text(encoding="utf-8"))
    library = FaceLibrary(fonts_root)
    plan = evaluate(ir, library)
    failures: list[str] = []
    block = plan["glyph_substitutions"]
    used = {entry["codepoint"]: entry for entry in block["used"]}

    # 1. The symbol fold, directly. Wingdings' bare U+00A7 and its private-use
    #    spelling are one glyph; Arial's U+00A7 is the section sign and must be
    #    left completely alone.
    if normalised_codepoint("Wingdings", "§") != WINGDINGS_SQUARE_BULLET:
        failures.append("Wingdings U+00A7 does not fold onto the PUA spelling, so "
                        "Arimo's SECTION SIGN would be drawn for a square bullet")
    if normalised_codepoint("Arial", "§") != 0x00A7:
        failures.append("a non-symbol family must not be folded into the PUA")

    # 2. THE item-1 assertion: a face that is asked for a codepoint it lacks is
    #    still judged only on the glyphs it has. Before the fix Arial|700 on this
    #    form reported a 0.74pt worst advance -- Arimo's gid 0 at 0.75em against
    #    Arial's real 0.604em -- and failed `within_measurement_precision`. That
    #    number was never a measurement of Arimo's advances.
    arial_bold = next((f for f in plan["faces"]
                       if f["face_key"] == "Arial|700|normal"), None)
    if arial_bold is None or arial_bold["status"] != "resolved":
        failures.append("1601C must resolve Arial|700|normal")
    else:
        check = arial_bold["metric_check"]
        worst = check["max_advance_delta_pt"]
        if worst is None or worst > SELF_TEST_MAX_DELTA_PT:
            failures.append(
                f"Arial|700|normal worst advance {worst}pt exceeds "
                f"{SELF_TEST_MAX_DELTA_PT}pt. If this is a .notdef leaking back "
                f"into the proof, fix the coverage test -- never widen this.")
        if not check["within_measurement_precision"]:
            failures.append(
                "Arial|700|normal is no longer within measurement precision; a "
                "placeholder advance is being measured as if it were the face's")
        coverage = arial_bold["glyph_coverage"]
        absent = {a["codepoint"]: a for a in coverage["absent_codepoints"]}
        circle = absent.get(format_codepoint(BLACK_CIRCLE))
        if circle is None:
            failures.append(
                "Arial|700|normal must still report U+25CF as absent; dropping "
                "an unrepresentable glyph hides a real rendering problem")
        else:
            if circle["count"] != SELF_TEST_ARIAL_BOLD_BLACK_CIRCLES:
                failures.append(
                    f"Arial|700|normal U+25CF count {circle['count']} != "
                    f"{SELF_TEST_ARIAL_BOLD_BLACK_CIRCLES}")
            if circle["status"] != SUBSTITUTED or circle["replacement"] != "U+2022":
                failures.append(f"U+25CF must be substituted by U+2022, got {circle}")
        # Nothing may be quietly discarded: measured + substituted +
        # unrepresentable has to account for every character set in this face.
        counted = (coverage["representable_samples"] + coverage["substituted_samples"]
                   + coverage["unrepresentable_samples"])
        chars = sum(len(r["text"]) for p in ir["pages"] for r in p["text_runs"]
                    if face_key(r["family"], r["bold"], r["italic"])
                    == arial_bold["face_key"])
        if counted != chars:
            failures.append(
                f"Arial|700|normal accounts for {counted} of {chars} characters; "
                f"absent glyphs must be counted, not dropped")

    # 3. Item 2: what the corpus actually contains, and what each maps to.
    circle = used.get(format_codepoint(BLACK_CIRCLE))
    if circle is None or circle["count"] != SELF_TEST_SUBSTITUTED_BLACK_CIRCLES:
        failures.append(
            f"expected {SELF_TEST_SUBSTITUTED_BLACK_CIRCLES} U+25CF on 1601C, got "
            f"{circle['count'] if circle else 0}")
    elif circle["status"] != SUBSTITUTED:
        failures.append(f"U+25CF must be substituted, got {circle['status']}")
    square = used.get(format_codepoint(WINGDINGS_SQUARE_BULLET))
    if square is None or square["count"] != SELF_TEST_UNREPRESENTABLE_SQUARES:
        failures.append(
            f"expected {SELF_TEST_UNREPRESENTABLE_SQUARES} U+F0A7 on 1601C, got "
            f"{square['count'] if square else 0}")
    elif square["status"] != UNREPRESENTABLE or square["replacement"] is not None:
        failures.append(
            f"U+F0A7 is a square and nothing bundled draws a square; it must stay "
            f"unrepresentable rather than borrow a disc, got {square}")

    # 4. Every absent codepoint is warned about by name. This is what stops an
    #    unrepresentable glyph disappearing into a count nobody reads.
    for codepoint, entry in used.items():
        if not any(codepoint in warning for warning in plan["warnings"]):
            failures.append(f"{codepoint} is absent but no warning names it")
        expected = SUBSTITUTED.upper() if entry["status"] == SUBSTITUTED \
            else UNREPRESENTABLE.upper()
        if not any(warning.startswith(expected) and codepoint in warning
                   for warning in plan["warnings"]):
            failures.append(
                f"{codepoint} is {entry['status']} but no warning says so; a "
                f"substitution and a hole are not the same severity")

    # 5. The pinned advances are the document's, not a transcription.
    for codepoint, substitution in sorted(GLYPH_SUBSTITUTIONS.items()):
        observed = pdf_advances_em(ir, codepoint)
        if not observed:
            continue
        drift = max(abs(value - substitution.source_advance_em) for value in observed)
        if drift > SOURCE_ADVANCE_TOLERANCE_EM:
            failures.append(
                f"{format_codepoint(codepoint)}: pinned source advance "
                f"{substitution.source_advance_em}em is {drift:.4f}em away from "
                f"what the PDF carries ({min(observed):.4f}..{max(observed):.4f}em)")

    # 6. Substitution must not break the layout model. The stand-in is narrower,
    #    the run's letter-spacing absorbs exactly that, and every glyph after it
    #    still lands on its PDF origin -- which is the only thing verify.py
    #    compares. A regression here is a page that shifts, not a report that
    #    reads oddly.
    substituted_runs = 0
    for entry in plan["runs"]:
        for record in entry.get("substitutions") or []:
            if record["status"] != SUBSTITUTED:
                continue
            substituted_runs += 1
            if record["advance_correction_pt"] <= 0:
                failures.append(
                    f"page {entry['page']} run {entry['run_index']}: the stand-in "
                    f"for {record['from']} is not narrower than the character it "
                    f"replaces, so the measured cost is wrong")
            # Model-aware for the same reason spacing_invariant_failures is: a
            # separated run answers for the glyph-origin span, not for a total
            # width that also contains the final glyph's own advance delta.
            residual = (entry["origin_span_residual_pt"]
                        if entry["spacing_model"] == "letter+word"
                        else entry["width_residual_pt"])
            if residual is not None and abs(residual) > LETTER_SPACING_ACCUMULATED_PT:
                failures.append(
                    f"page {entry['page']} run {entry['run_index']}: substituted "
                    f"run misses its {entry['spacing_model']} target by "
                    f"{residual}pt")
            break
    if substituted_runs != SELF_TEST_ARIAL_BOLD_BLACK_CIRCLES:
        failures.append(
            f"{substituted_runs} runs carry a substitution, expected "
            f"{SELF_TEST_ARIAL_BOLD_BLACK_CIRCLES}")

    # 7. The faces whose tracking was excluded are exactly those runs, and the
    #    surviving tracking figures are still ordinary.
    for face in plan["faces"]:
        track = face.get("tracking_check") or {}
        if face["face_key"] != "Arial|700|normal":
            if track.get("runs_excluded_for_substitution"):
                failures.append(
                    f"{face['face_key']} excluded runs for a substitution it "
                    f"does not have")
            continue
        if track.get("runs_excluded_for_substitution") != SELF_TEST_ARIAL_BOLD_BLACK_CIRCLES:
            failures.append(
                f"Arial|700|normal should hold back "
                f"{SELF_TEST_ARIAL_BOLD_BLACK_CIRCLES} substituted runs from its "
                f"tracking figures, got {track.get('runs_excluded_for_substitution')}")

    # 7b. Every substitution actually applied is served by the package whose
    #     metrics the pinned costs describe. A face served from elsewhere would
    #     still get a bullet, but the "58% of the diameter, 58% of the advance"
    #     the plan reports for it would be someone else's measurement.
    for face in plan["faces"]:
        coverage = face.get("glyph_coverage") or {}
        if not any(a["status"] == SUBSTITUTED
                   for a in coverage.get("absent_codepoints", [])):
            continue
        package = face["fontsource_package"] or ""
        if not package.endswith(f"/{SUBSTITUTION_REFERENCE_PACKAGE}"):
            failures.append(
                f"{face['face_key']} applies a substitution but is served by "
                f"{package!r}, not {SUBSTITUTION_REFERENCE_PACKAGE}; the pinned "
                f"substitution costs do not describe that face")

    # 7c. The spacing model's own invariants hold with substitutions present too.
    failures.extend(spacing_invariant_failures(plan))

    # 8. The table still matches the shipped fonts, and the plan is deterministic
    #    with substitutions in it.
    failures.extend(substitution_table_failures(library))
    again = json.dumps(evaluate(ir, FaceLibrary(fonts_root)), sort_keys=False)
    if again != json.dumps(plan, sort_keys=False):
        failures.append("output is not deterministic across runs")

    print_face_table(plan, sys.stdout)
    print("", file=sys.stdout)
    for warning in plan["warnings"]:
        if warning.startswith((SUBSTITUTED.upper(), UNREPRESENTABLE.upper())):
            print(f"  - {warning}", file=sys.stdout)

    print("", file=sys.stdout)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stdout)
        return 1
    print(f"substitution self-test OK: {block['substituted_occurrences']} "
          f"substituted, {block['unrepresentable_occurrences']} unrepresentable "
          f"on {plan['form']['code']}-{plan['form']['revision']}", file=sys.stdout)
    return 0


def unmapped_self_test(ir_paths: Sequence[pathlib.Path],
                       fonts_root: pathlib.Path | None) -> int:
    """Assert the glyph-id fold and the word-spacing split on the real 2553/2550M.

    The two halves above cannot reach either. 2551Q sets nothing the shipped
    faces lack and nothing the generator justified; 1601C's Wingdings font
    carries a usable encoding, so its square bullet arrives already spelled
    U+F0A7 and the glyph-id path is never taken. These two sheets are the only
    ones where `get_text("rawdict")` guessed a codepoint outright -- and 2553 is
    where the single-number spacing model does its worst visible damage -- so
    this is the document set that holds both fixes to their evidence.
    """
    if fonts_root is None:
        print("unmapped self-test needs node_modules/@fontsource-variable",
              file=sys.stderr)
        return 2
    missing = [str(path) for path in ir_paths if not path.is_file()]
    if missing:
        print(f"unmapped self-test needs the real IRs at {', '.join(missing)}",
              file=sys.stderr)
        return 2

    library = FaceLibrary(fonts_root)
    failures: list[str] = []

    # 1. The glyph-id fold itself, before any document. Glyph 131 of Wingdings is
    #    the square bullet; an id the table does not name must stay U+FFFD rather
    #    than borrow the nearest entry.
    folded = run_codepoints({"family": "Wingdings", "text": chr(UNMAPPED_CODEPOINT),
                             "unmapped_glyphs": [{"index": 0, "glyph_id": 131,
                                                  "rawdict_codepoint": 0xA7}]})
    if folded != [WINGDINGS_SQUARE_BULLET]:
        failures.append(
            f"Wingdings glyph 131 must fold onto "
            f"{format_codepoint(WINGDINGS_SQUARE_BULLET)}, got "
            f"{[format_codepoint(c) for c in folded]}")
    unknown = run_codepoints({"family": "Wingdings", "text": chr(UNMAPPED_CODEPOINT),
                              "unmapped_glyphs": [{"index": 0, "glyph_id": 9999,
                                                   "rawdict_codepoint": 0xA7}]})
    if unknown != [UNMAPPED_CODEPOINT]:
        failures.append(
            f"an unnamed glyph id must stay {format_codepoint(UNMAPPED_CODEPOINT)} "
            f"and be reported unrepresentable, got "
            f"{[format_codepoint(c) for c in unknown]}")

    # 2. U+FFFD is never looked up in a face. Arimo has it, so a coverage test
    #    would call it present and measure a 0.8403em placeholder as if it were
    #    the source glyph's advance.
    arimo = library.load(SUBSTITUTION_REFERENCE_PACKAGE, 400, "normal")
    if arimo is None:
        failures.append("could not load arimo to check the U+FFFD guard")
    else:
        if not arimo.has(UNMAPPED_CODEPOINT):
            failures.append(
                "arimo no longer contains U+FFFD, so UNSTATED_CODEPOINTS is "
                "guarding against something that cannot happen; re-derive it")
        guarded = resolve_glyph(arimo, UNMAPPED_CODEPOINT, chr(UNMAPPED_CODEPOINT))
        if guarded.status != UNREPRESENTABLE or guarded.drawn_codepoint is not None:
            failures.append(
                f"U+FFFD resolved to {guarded.status} drawing "
                f"{guarded.drawn_codepoint!r}; a glyph the source did not name "
                f"has no advance to measure and must never enter the proof")

    plans: dict[str, dict[str, Any]] = {}
    for path in ir_paths:
        ir = json.loads(path.read_text(encoding="utf-8"))
        plan = evaluate(ir, library)
        code = plan["form"]["code"]
        plans[code] = plan
        used = {entry["codepoint"]: entry for entry in
                plan["glyph_substitutions"]["used"]}

        # 3. The seven glyphs, per form, reported as the square and not as a
        #    section sign and not as a replacement mark.
        expected = SELF_TEST_UNMAPPED_SQUARES.get(code)
        square = used.get(format_codepoint(WINGDINGS_SQUARE_BULLET))
        if expected is None:
            failures.append(f"no pinned glyph-131 count for {code}")
        elif square is None or square["count"] != expected:
            failures.append(
                f"{code}: expected {expected} occurrences of "
                f"{format_codepoint(WINGDINGS_SQUARE_BULLET)}, got "
                f"{square['count'] if square else 0}. rawdict reports these as "
                f"U+00A7 SECTION SIGN; if the count is 0 the glyph id is no "
                f"longer being read and the sheet is printing a section sign.")
        elif square["status"] != UNREPRESENTABLE or square["replacement"] is not None:
            failures.append(
                f"{code}: {format_codepoint(WINGDINGS_SQUARE_BULLET)} is a filled "
                f"square and nothing bundled draws one; it must stay "
                f"unrepresentable rather than borrow a disc, got {square}")
        if format_codepoint(UNMAPPED_CODEPOINT) in used:
            failures.append(
                f"{code}: {format_codepoint(UNMAPPED_CODEPOINT)} survived to the "
                f"report, so a drawn glyph is unaccounted for rather than folded "
                f"onto the entry that describes it")
        if 0x00A7 in {ord(c) for page in ir["pages"]
                      for run in page["text_runs"] if run["family"] == "Wingdings"
                      for c in run["text"]}:
            failures.append(
                f"{code}: a Wingdings run still carries U+00A7, which is what "
                f"rawdict guessed; extract.py should be carrying U+FFFD plus the "
                f"glyph id instead")

        # 4. Every one is warned about by name and by severity.
        for codepoint, entry in used.items():
            if not any(warning.startswith(UNREPRESENTABLE.upper())
                       and codepoint in warning for warning in plan["warnings"]
                       if entry["status"] == UNREPRESENTABLE):
                failures.append(f"{code}: {codepoint} is absent but no "
                                f"UNREPRESENTABLE warning names it")

        # 5. The pinned source advance is this document's too. pdf_advances_em
        #    applies the same fold, so the U+F0A7 pin is now validated against
        #    the spelling that carries no codepoint at all.
        for codepoint, substitution in sorted(GLYPH_SUBSTITUTIONS.items()):
            observed = pdf_advances_em(ir, codepoint)
            if not observed:
                continue
            drift = max(abs(v - substitution.source_advance_em) for v in observed)
            if drift > SOURCE_ADVANCE_TOLERANCE_EM:
                failures.append(
                    f"{code}: pinned source advance "
                    f"{substitution.source_advance_em}em for "
                    f"{format_codepoint(codepoint)} is {drift:.4f}em away from "
                    f"what the PDF carries ({min(observed):.4f}.."
                    f"{max(observed):.4f}em)")

        # 6. The spacing model's invariants, and determinism, on both documents.
        failures.extend(f"{code}: {failure}"
                        for failure in spacing_invariant_failures(plan))
        again = json.dumps(evaluate(ir, FaceLibrary(fonts_root)), sort_keys=False)
        if again != json.dumps(plan, sort_keys=False):
            failures.append(f"{code}: output is not deterministic across runs")

    # 7. S6, measured on the sheet it was written against. The claim is not that
    #    2553 now lays out perfectly -- it does not, and the residual is
    #    reported -- but that the justification is being read as justification.
    serif = next((face for face in plans["2553"]["faces"]
                  if face["family"] in ("TimesNewRoman", "Times New Roman")
                  and face["css_weight"] == 400
                  and face["status"] == "resolved"), None)
    if serif is None:
        failures.append("2553 must resolve a Times New Roman 400 face")
    else:
        track = serif["tracking_check"] or {}
        spacing = track.get("letter_spacing_em") or {}
        worst_letter = max(abs(spacing.get("min") or 0.0),
                           abs(spacing.get("max") or 0.0))
        if worst_letter > SELF_TEST_MAX_LETTER_SPACING_EM:
            failures.append(
                f"2553 {serif['face_key']}: letter tracking reaches "
                f"{worst_letter}em, over {SELF_TEST_MAX_LETTER_SPACING_EM}em. That "
                f"is the justification being smeared across the glyphs again -- "
                f"the single-number model reached 0.1221em here.")
        if (track.get("runs_word_spaced") or 0) < SELF_TEST_MIN_WORD_SPACED_RUNS:
            failures.append(
                f"2553 {serif['face_key']}: only {track.get('runs_word_spaced')} "
                f"runs carry word-spacing, expected at least "
                f"{SELF_TEST_MIN_WORD_SPACED_RUNS}; its page 2 is justified prose")
        if not track.get("word_spacing_em"):
            failures.append(f"2553 {serif['face_key']}: no word-spacing reported")
        drift = track.get("origin_drift") or {}
        emitted = drift.get("max_origin_drift_pt")
        uniform = drift.get("max_origin_drift_pt_if_uniform")
        if not emitted or not uniform:
            failures.append(f"2553 {serif['face_key']}: no origin drift reported")
        else:
            recovery = (uniform - emitted) / uniform
            if recovery < SELF_TEST_MIN_DRIFT_RECOVERY:
                failures.append(
                    f"2553 {serif['face_key']}: separating the two spacings "
                    f"recovers only {recovery:.1%} of the worst glyph-origin "
                    f"error ({uniform}pt -> {emitted}pt), under the pinned "
                    f"{SELF_TEST_MIN_DRIFT_RECOVERY:.0%}")

    for code, plan in plans.items():
        print(f"== {code}-{plan['form']['revision']}", file=sys.stdout)
        print_face_table(plan, sys.stdout)
        for warning in plan["warnings"]:
            if warning.startswith((UNREPRESENTABLE.upper(), "ORIGIN DRIFT")):
                print(f"  - {warning}", file=sys.stdout)
        print("", file=sys.stdout)

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stdout)
        return 1
    total = sum(SELF_TEST_UNMAPPED_SQUARES.values())
    print(f"unmapped/word-spacing self-test OK: {total} glyph-131 occurrences "
          f"resolved to {format_codepoint(WINGDINGS_SQUARE_BULLET)} across "
          f"{', '.join(sorted(plans))}, none printing a section sign",
          file=sys.stdout)
    return 0


def locate_ir(here: pathlib.Path, relative: pathlib.Path) -> pathlib.Path | None:
    """Find a build IR from either the repo root or the tools directory."""
    for candidate in (here.parent.parent / relative, relative):
        if candidate.is_file():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ir", type=pathlib.Path, default=None,
                        help="IR JSON from extract.py")
    parser.add_argument("--out", type=pathlib.Path, default=None,
                        help="Write the font plan here (default: stdout).")
    parser.add_argument("--fonts-root", type=pathlib.Path, default=None,
                        help="Checkout holding node_modules/@fontsource-variable "
                             "(default: nearest ancestor that has it).")
    parser.add_argument("--summary", action="store_true",
                        help="Print the per-face advance-delta table to stderr.")
    parser.add_argument("--derive-narrow-scale", action="store_true",
                        help="Measure the Arial Narrow scaleX from the macOS "
                             "system faces instead of using the pinned constant. "
                             "Makes the plan machine-dependent; for auditing the "
                             "pin, not for building.")
    parser.add_argument("--self-test", action="store_true",
                        help="Assert the plan against the real 2551Q IR.")
    args = parser.parse_args(argv)

    here = pathlib.Path(__file__).resolve().parent
    fonts_root = args.fonts_root or find_fonts_root(here)

    if args.self_test:
        # An explicit --ir still means "run the metric self-test against this
        # one document", as it always has. The two-document default is only the
        # default: each half pins counts for the form it was written against and
        # neither is meaningful pointed at an arbitrary IR.
        if args.ir is not None:
            return self_test(args.ir, fonts_root)
        ir_path = locate_ir(here, SELF_TEST_IR)
        substitution_ir = locate_ir(here, SELF_TEST_SUBSTITUTION_IR)
        unmapped_irs = [locate_ir(here, relative)
                        for relative in SELF_TEST_UNMAPPED_IRS]
        wanted = [(ir_path, SELF_TEST_IR), (substitution_ir, SELF_TEST_SUBSTITUTION_IR),
                  *zip(unmapped_irs, SELF_TEST_UNMAPPED_IRS)]
        absent = [str(relative) for found, relative in wanted if found is None]
        if absent:
            print(f"self-test could not locate {', '.join(absent)}", file=sys.stderr)
            return 2
        assert ir_path is not None and substitution_ir is not None
        # Every half always runs and every status is kept: a coverage regression
        # must not hide behind a passing 2551Q, a 2551Q failure must not stop the
        # substitution report being printed, and neither may suppress the
        # glyph-id and word-spacing evidence, which is the only place the two
        # newest fixes are measured at all.
        status = self_test(ir_path, fonts_root)
        print("", file=sys.stdout)
        status = substitution_self_test(substitution_ir, fonts_root) or status
        print("", file=sys.stdout)
        return unmapped_self_test(
            [path for path in unmapped_irs if path is not None], fonts_root) or status

    if args.ir is None or not args.ir.is_file():
        print(f"no such IR: {args.ir}", file=sys.stderr)
        return 2
    if fonts_root is None:
        print("could not locate node_modules/@fontsource-variable; pass --fonts-root",
              file=sys.stderr)
        return 2

    ir = json.loads(args.ir.read_text(encoding="utf-8"))
    overrides, provenance = scale_overrides(args.derive_narrow_scale)
    plan = evaluate(ir, FaceLibrary(fonts_root), overrides, provenance)
    payload = json.dumps(plan, indent=2, ensure_ascii=False) + "\n"

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)

    if args.summary:
        print_face_table(plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
