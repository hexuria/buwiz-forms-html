# formgen — PDF-native form conversion

A deterministic pipeline that converts one pinned BIR form PDF into an HTML
renderer by **reading the PDF's content stream**, not by looking at pictures of
it.

## Why this exists

The previous approach rasterised the official PDF to PNG and pixel-diffed it
against a Chromium screenshot of our HTML. That measurement made glyph outline
shape roughly 57% of the residual, and because the source PDFs do not embed
their primary faces, the "official" raster actually encoded *Poppler's
substituted glyphs* rather than BIR's typography. The gate was therefore
unreachable by proof, and 273 commits went into trying anyway.

The premise was wrong, not the effort. A PDF is a vector document. Everything
the previous approach was trying to *infer from pixels* is written down in the
file:

| Question | Raster answer | Content-stream answer |
| --- | --- | --- |
| Where is this border? | cluster dark pixels, guess | `re` operator, exact pt |
| How thick is it? | count pixels, off-by-one | exact `height` of the filled rect |
| Is it a rule or grey decoration? | ink presence — indistinguishable | literal fill value `0.0` vs `0.8509` |
| Which of two overlapping rects wins? | whatever the pixels ended up as | its position in the content stream |
| What font is this? | unknowable, it was substituted | `/BaseFont /Arial,Bold` |
| What size? | measure and guess | `Tf` operand |
| Custom letter spacing? | invisible | glyph origins vs `/Widths` |

So: stop rasterising. Extract, generate, and verify entirely in vector space.

## The pipeline

```
pinned PDF ──extract.py──► form IR (geometry + typography, exact pt)
                              │
                              ├──lattice.py──► box model: cells, regions, growable bands, combs
                              ├──fonts.py────► font plan: CSS face per run + advance-metric proof
                              ├──guides.py──► guide plan: where the sheet stops being form
                              │
                              └──emit.py─────► HTML + CSS (absolute pt layout, @page = MediaBox)
                                                  │
                                     Chromium print-to-PDF
                                                  │
                                              extract.py  ← the same extractor
                                                  │
                                             verify.py ──► IR-vs-IR numeric diff
```

`batch.py` drives that loop over all 51 source PDFs and packages the result as
the hand-maintainable tree under `forms/`. Above the per-form loop sits a
verification stack that came later and is documented in the module map below:
`audit.py` round-trips and scores every generated form and asserts the eight
properties scoring cannot see, `comb_referee.py` re-measures every comb with a
deliberately independent implementation, `validate_tree.py` checks the
committed `forms/` tree using nothing else, and `gate.py` runs all of it as the
single done-condition.

The last step is the important one. **We compare extraction to extraction, not
raster to raster.** Our HTML is printed to PDF by Chromium, that PDF is parsed
by the *same* extractor, and the two IRs are diffed numerically:

- borders → matched by position and thickness, tolerance 0.25pt / 0.05pt
- text → matched by content, then compared on family, weight, style, size,
  origin, baseline and advance width
- images → matched by SHA-256, then by placement
- paper → exact equality, any difference fails immediately

Glyph outlines never enter the comparison. The unembedded-font problem does not
apply to a comparison that never rasterises a glyph.

## What "identical fonts" means here, precisely

The PDF gives exact family, style, weight and size. Those are reproduced
exactly. What cannot be reproduced is the *outline data* of a face that was
never embedded — but that was never what layout depended on.

Layout depends on **advance widths**. Arimo is metrically identical to Arial by
design, so `Arial 9pt` and `Arimo 9pt` place every subsequent glyph at the same
x. `fonts.py` proves this per run rather than assuming it: it computes what the
substitute face would advance and compares against the advance the PDF actually
recorded. If the deltas are not near zero, the substitution is wrong and we find
out on run one instead of after 273 commits.

Custom tracking is recovered the same way: measured advance minus the face's
natural advance, spread across the gaps, is the `letter-spacing` the generator
applied. Line height comes from the span's own ascender/descender, never from
the browser default.

The same argument, and the same proof, carries the corpus's serif: **Times New
Roman resolves to Tinos**, its metric clone from the same Chrome OS core-fonts
commission as Arimo and under the same Apache-2.0 terms. Tinos ships as four
static faces rather than one variable file, which is why a package is described
by candidate paths (`@fontsource/<name>` as well as `@fontsource-variable/<name>`)
and why the `@font-face` weight descriptor is derived from the loaded face's own
`fvar` instead of being the constant `100 900` — a static regular declared over
the whole range makes the browser synthesise the bold, inventing advances the
proof never covered.

A family with no mapping stays UNRESOLVED and is warned about by name, and so
does a mapped family whose package is not installed: that warning names the
package that would fix it. Nothing is ever quietly served by a different family.
Wingdings, Symbol, Tahoma, Candara and Berlin Sans FB Demi are a few hundred
characters of dingbats and bullets between them; they need per-character
substitution, not a font, and are deliberately left unresolved.

## Paper size

`@page` is set from the PDF's own MediaBox, per form. Across the 51-form
corpus: 42 forms are 612×936pt (Folio), one — the 1701 consolidation sheet —
is Folio turned landscape at 936×612, four are 612×1008 (Legal: 2550M, 2550Q,
2551M, 2553) and four are 612×792 (Letter: 0619E, 0619F, 0620, the 1701
attachment). None of them are A4. Forcing a single paper size would distort
every form off its official dimensions, so paper is per-form data — which is
also what lets one script handle all 51 without a table of special cases.

## Growable fields

Boxes are found first, then the ones that repeat are recognised as growable.
`lattice.py` looks for maximal runs of consecutive cell-rows sharing an
identical column signature at a constant vertical pitch. That is what an ATC
table, a schedule of income, or a list of creditable taxes *is* in the drawing:
the same row stamped N times.

Each growable band is emitted with its pitch, its official on-sheet capacity,
and one template row. At render time a band holds up to `capacity` rows on the
sheet; beyond that it spills to a continuation page, which is what the official
form does and what the existing `2551q-10-rows` fixture encodes. Everything
outside the band stays absolutely positioned, so growth cannot disturb the rest
of the page.

## Paint order

Two rects can overlap, and then the one painted later wins. That is a fact of
the content stream, not something to infer, so `extract.py` records the ordinal
of the op that painted every rule, area fill and image, and `emit.py` paints the
rule layer in exactly that order.

The order has to come from the *op*, not from the drawing: a path that both
fills and strokes is one `get_drawings()` entry and two paint ops, and PDF draws
the fill first and the outline over it. `get_bboxlog()` is the one view that
lists fills, strokes and images separately in stream order, so the ordinal comes
from there and is reconciled against `get_drawings()`; a mismatch raises rather
than falling back, because a plausible document with the wrong z-order is worse
than no document.

`emit.py` used to bucket the layer as fills → decorative greys → structural
black. That is a guess about z-order, and it is wrong wherever a form paints a
*lighter* rect after a darker one. 2552 draws the white knockout inside each
checkbox at op 4776 and a grey row separator crossing it at op 173, so the
bucket order put a light-grey line through every checkbox on the sheet. Tone
cannot tell you which rect is on top. Only the stream can.

## Anti-aliasing

The rule layer is anti-aliased. `shape-rendering="crispEdges"` looks like the
right choice for a page made of thin rules and it is not: it disables
anti-aliasing, so coverage becomes all-or-nothing against the pixel centre and a
rule thinner than one device pixel does not get sharper, it disappears. At
device_scale_factor 1 that erased every 0.24pt comb divider on 2552 page 1 —
which is to say, it erased the comb.

Anti-aliasing also happens to be what the official raster does, so a sub-pixel
rule lands as the mid-grey tone the source produces. Printing is unaffected
either way: print-to-PDF keeps the rects as vectors, and the IR round-trip does
not change by a pixel.

## Combs

A comb field (per-character boxes for TIN, dates, amounts) is drawn as an
enclosing box plus N−1 equally spaced 0.24pt dividers. It is emitted as **one
cell with N slots**, never as N separate containers — it is one field that
happens to be drawn with tick marks.

These 0.24pt dividers are exactly what the old raster pipeline saw as mid-grey
tone 83–153 and could not distinguish from decoration, because sub-pixel black
ink cannot fill a pixel at 144 DPI. In the content stream they are pure black at
an exact coordinate. The ambiguity was an artefact of the measurement.

## Fields

A cell of kind `field` is a box the sheet left blank, and it carries a real
`<input>`: one per plain field, one per comb slot. Per slot, because the slot is
already a positioned box at a *measured* `slot_x`, so a centred character lands
on a measured slot centre by layout rather than by an advance calculation over a
pitch that is not uniform. A comb stays **one** field: all of its inputs share
the cell's `name`, and the cell carries `data-field-kind`, `data-field-name` and
`data-comb-capacity` so a binder can address the field without re-deriving any
geometry. Ids are `<cell>-i` and `<cell>-s<n>` — page, cell and slot;
deterministic, and stable across a re-render.

Typing runs forward through a comb and backspace runs back through it. Every
listener is delegated from the document, which is what makes a row added by
`setBandRows` fillable with no re-binding step, and that is not a small saving:
2551Q page 1 alone has 488 comb inputs.

Three properties keep the field layer from costing anything on paper:

- **An empty field prints as nothing.** Every affordance is a `:hover`/`:focus`
  rule under `@media screen`, and print has neither state, so an empty sheet
  prints as the same PDF it did before fields existed — even if a stylesheet
  transform loses the media guard, which had happened to the packaged bundles.
- **A filled field prints only its characters.** `@media print` strips border,
  outline, background and box-shadow. The caret needed catching separately:
  printing with the cursor still in a comb painted a 0.75×6.75pt black bar, and
  the round-trip reported it as an extra structural rule — correctly, because on
  paper that is what it is. `caret-color` does not suppress it in Chromium's
  print path, so the focus is dropped on `beforeprint`.
- **The typography is the font plan's.** A blank has no text to measure, so the
  face is the sheet's own modal body face by glyph count, restricted to resolved
  metric-compatible faces at unit scale, and the size is fitted to the box the
  source drew — a comb's own sub-band, or a cell inset by the thickness of the
  rule bounding each side — and never exceeds the body size. An unstyled
  `<input>` types in the platform UI font at 13.33px, which is a different
  document from the one it is being typed onto.

`fill_check.py` is the proof, and it is IR-vs-IR like everything else: fill every
field, type one comb by keystroke, print, re-extract, and compare each glyph's
centre against the layout's measured slot centre.

## One sheet, two documents

A BIR sheet carries a form and a pile of reference material — ATC tables,
"Guidelines and Instructions", penalty schedules — printed on the same paper.
`guides.py` finds where the second one starts (17 of the 51 forms have an inline
guide region, mean 67% of the page it sits on) and `emit.py` emits either half:

```sh
python3 tools/formgen/emit.py --ir ... --layout ... --font-plan ... \
  --guide-plan build/guides/1603q-2018.guide.json \
  --document form  --out build/html/1603q-2018.html
python3 tools/formgen/emit.py --ir ... --layout ... --font-plan ... \
  --guide-plan build/guides/1603q-2018.guide.json \
  --document guide --out build/html/1603q-2018.guide.html
```

Three rules make the split free:

- **The form's page boxes never change.** A page whose lower 70% became empty
  keeps its full height, its place in the page count and its `@page` size. The
  freed space is what a growable band expands into; moving the page box to
  reclaim it would move every coordinate below.
- **Straddlers belong to the form.** An element crossing the cut is claimed by
  nobody, so it stays. Losing a rule off the form is a geometry regression; a
  duplicated rule on the guide is cosmetic. A growable band is indivisible and
  is awarded to the form whole for the same reason.
- **With no `--guide-plan` the output is byte-identical to what it was.**
  Measured across the corpus: 22 forms byte-identical, 17 shrink by exactly what
  their guide claimed, 12 gain only the 283-character cross-link.

The cross-link is `<a class="doc-link">`, absolutely positioned (so it is out of
flow and cannot push a page down) and `display:none` in print (so the document
verify.py measures does not contain it).

The guide does not need parity and one page is actively wrong with it: 1603Q's
guideline block is two columns of 6pt prose, and placing those as positioned
runs is what makes them overlap. `--guide-layout reflow` (the guide's default)
finds the columns from the run x-distribution — gutters at ≤12% of the page's
own peak coverage, then narrow slivers dissolved away — groups the runs into
reading order and emits flowing headings and paragraphs. A region whose columns
put ink on less than 60% of their width is a table, not prose, and is emitted
row-major as a real table on the undissolved gutter grid; across the 17 regions
that separates the two ATC tables (0.36–0.55) from the thirteen prose blocks
(0.61–0.95) with nothing in between. `--guide-layout absolute` keeps the
positioned form for anyone who wants the original arrangement.

### Guides are printed, not only read

A guide is the document someone puts beside the form they are filling in, so it
has to come out of a printer. Three things were in the way and all three are
stylesheet or document-structure problems, not geometry:

- **No `@page`.** A guide printed at whatever the browser defaults to, which is
  Letter wherever the user is, while its form prints Folio or Legal. The guide
  now sets `@page` from the *form's* paper — a form and its instructions should
  be one stack of paper, not two — with a 36pt margin instead of the form's
  `margin:0`. The form's zero margin is right for a sheet whose every coordinate
  is measured from the MediaBox and wrong for prose, which would otherwise run
  into the unprintable border every consumer printer reserves.
- **Pagination.** `@media print` keeps headings with what they introduce
  (`break-after:avoid`), keeps table rows and short blocks whole, sets
  orphans/widows, repeats a table's header group across sheets, and drops the
  cross-document navigation links, which mean nothing on paper. The prose then
  flows across as many sheets as it needs.
- **The twelve standalone guide PDFs.** They used to be embedded with
  `<object>`. An embedded PDF is a second document with its own pagination:
  printing the page around it prints the plugin's viewport, so 1701Q's four
  pages of instructions printed as one near-blank sheet with the file name on
  it. They are now run through *this* pipeline — `extract.py`, then `emit.py
  --document guide --guide-layout reflow` via `--guide-source` — so their text
  becomes reflowed HTML exactly like an inline guide, and all 29 guides print
  the same way. That is a reading copy, not a replacement: the pinned PDF stays
  in the bundle's `guides/` directory and is linked from the document, and it
  remains the exact artefact. No lattice, no font plan and no parity score is
  computed for it; a guide has no fields to model and nothing to measure.

`batch.py`'s CSS splitter had to learn about nesting before any of this could
work. It flattened a stylesheet with one regex over `sel{body}`, which cannot
see `@media print{…}` and so lifted the rules inside it out of their condition —
`.doc-link{display:none}` had been unconditional in all 29 bundles since the
split existed, hiding both documents' navigation links on screen. Every rule
above would have been applied to the screen the same way.

## Determinism

Same PDF in, byte-identical HTML out. No timestamps, no randomness, no
dict-order dependence, no hand tuning per form. That is the property that made
converting the rest of the corpus a matter of running the script, and it is the
thing to protect above any individual form's score. The gate does not take it
on faith: a full run regenerates the corpus twice and compares the two
generations byte for byte.

## Usage

```sh
python3 tools/formgen/extract.py \
  --pdf "/path/2551Q Jan 2018 ENCS final rev 3_copy.pdf" \
  --form-code 2551Q --revision 2018 \
  --expected-sha256 1f270ecf66d778836a14697863e420ff65d5ed0a5576a6cf58b97c9a8e8c9b24 \
  --out build/ir/2551q-2018.ir.json --summary
```

`--expected-sha256` is not optional in real runs. Every downstream artefact is
only meaningful relative to an exactly pinned source.

The corpus driver is one command — `python3 tools/formgen/batch.py` — which
reads the source PDFs from `--source-root` (default `~/Downloads/forms`),
stages intermediates under `build/` and packages bundles under `forms/`. Note
`ARCHIVED.md`: `forms/` is hand-maintained since the one-shot generation on
2026-07-29, so re-running batch over an edited bundle regenerates it from
source. The gate re-runs batch deliberately, as its determinism proof; a hand
edit that a regeneration would destroy does not belong in `forms/`.

## Module map

Everything lives flat in `tools/formgen/`. One line each; the sections above
explain the why.

- `extract.py` — pinned PDF → IR: geometry, typography, images, paint order,
  all in exact pt. Its `--self-test` asserts against six pinned official PDFs;
  `--self-test --fixtures` runs the same checks over the committed synthetic
  corpus instead, which is what CI can evaluate.
- `lattice.py` — the IR's flat rule list → box model: cells, regions, growable
  bands, comb slots.
- `fonts.py` — font plan: a shipped CSS face per run, with the advance-metric
  proof and letter-spacing recovery described above.
- `guides.py` — decides where a sheet stops being form and becomes reference
  material; writes the guide plan `emit.py` splits on.
- `emit.py` — IR + box model + font plan (+ guide plan) → self-contained
  HTML/CSS; `--document form|guide` picks which half.
- `batch.py` — drives the whole pipeline over every source PDF and packages
  `forms/`: shared `base.css`, fonts once under `forms/fonts/`, composited
  artwork under `forms/assets/` with every digest recorded in
  `forms/assets-manifest.json`.
- `verify.py` — the round-trip differ: print our HTML to PDF via Chromium,
  re-extract with the same extractor, diff IR against IR numerically.
- `audit.py` — round-trips every generated form, scores it, and asserts the
  eight properties scoring cannot see; writes `build/audit.json` with
  per-assertion offender detail per form.
- `comb_referee.py` — independent vector referee for printed comb
  compartments: parses Poppler's `pdftocairo -svg` output with the stdlib,
  never imports the producers it judges, and pins them by sha256 (editing
  `audit.py`, `extract.py` or `lattice.py` requires re-pinning in the same
  commit). Writes its ledger to `build/comb-referee.json` and its attestation
  to `build/comb-referee-attested.json`.
- `fill_check.py` — fills every field in a browser, types one comb by
  keystroke, prints, re-extracts, and compares each glyph centre to the
  layout's measured slot centre.
- `band_drive.py` — drives every growable band at 1, capacity and capacity+4
  rows.
- `index_page.py` — renders `forms/index.html` from the machine-produced
  reports.
- `validate_tree.py` — validates the committed `forms/` tree using nothing but
  the committed tree (no PDFs, no `build/`); verifies every staged asset
  against `forms/assets-manifest.json`. This is the part of the work a fresh
  clone can evaluate, so it is the backbone of CI.
- `gate.py` — the done-condition. See below.
- `field_identity.py` — durable fillable-cell catalog (9990 records). Not a mapper.
- `join_census.py` — read-only R1–R7 join census. Never writes `name=`.
- `leftover_keys.py` — read-only leftover `serialized_key` census. Unique
  leftovers are inventory facts, not joins. Never writes `name=`.
- `map_tin.py` — Stage 3 TIN mapper: copies the 163 R1 harvest keys onto
  input `name=` in `forms-corrected/` only. Fail-closed; never invents a key.
- `fixtures/make_fixtures.py` — builds the committed synthetic PDF corpus
  (`flip`, `glyphs`, `lean`, `masks`, `paths`, `rules`); `--verify` proves the
  committed bytes rebuild exactly.
- `fixtures/prove_fixtures_fail.py` — the check that reads the checkers: it
  mutates the fixture corpus, re-pins to the mutated digests so a hash mismatch
  cannot stand in for the result, and requires each mutation to trip exactly
  its own check. A neutered assertion trips nothing and fails this.

`ARCHIVED.md` records that the one-shot generation of `forms/` happened on
2026-07-29 and why the tree is hand-maintained from that point.
`local-runners/` holds machine-local full-gate launchers and their reports;
only its README is tracked.

## How to verify

```sh
python3 tools/formgen/gate.py                     # the done-condition, ~60 min
python3 tools/formgen/gate.py --skip-regenerate   # score forms/ as it stands
python3 tools/formgen/gate.py --only assertions   # one check while iterating
python3 tools/formgen/gate.py --list              # the check names
python3 tools/formgen/validate_tree.py --verbose  # the committed tree alone
python3 tools/formgen/<module>.py --self-test     # any of the twelve self-testing modules
python3 tools/formgen/fixtures/make_fixtures.py --verify
python3 tools/formgen/fixtures/prove_fixtures_fail.py
python3 tools/formgen/extract.py --self-test --fixtures
```

**The gate.** `gate.py` runs twelve checks and exits 0 only when all pass:
self-tests, conversion, rules, paper, artwork, text, assertions, findings,
tracked-files, audit-refresh, determinism, comb-referee. A full run first
regenerates the corpus twice and byte-compares the generations (determinism),
audits the final bytes (audit-refresh feeds rules/paper/artwork/text/
assertions), and runs the comb referee exactly last so nothing can stale its
evidence. The rule that matters most: **a check that cannot be evaluated is a
failure, never a pass** — `UNEVALUABLE` counts with the failures. Two
mechanical notes: `audit-refresh` exists only in full runs, so it is not an
`--only` choice; and a full run's `--json` target must be outside the
repository, or the write would stale the gate's own final snapshot.

**Self-tests.** `gate.py` declares `SELF_TEST_MODULES` — twelve modules expose
`--self-test`: extract, lattice, fonts, guides, emit, verify, index_page,
audit, comb_referee, gate, field_identity, map_tin. Five of them (lattice,
fonts, guides, emit, verify) assert against the real pinned corpus by
construction and cannot run on a fresh clone; extract defaults to its official
pins but accepts `--fixtures`; the remaining seven (index_page, comb_referee,
audit, gate, field_identity, map_tin, plus extract's fixture profile) need no
external input beyond a Chromium for audit.

**CI** (`.github/workflows/formgen.yml`) runs the no-external-input subset on
every push: the tree validator and its own self-test, fixture-corpus
determinism (two generations, byte-compared) and committed-bytes verification,
`prove_fixtures_fail.py`, the six no-input module self-tests (index_page,
comb_referee, audit, gate, field_identity, map_tin — audit drives a real
Chromium), and extract's fixture profile. **CI is not the gate and says so in
its job summary every run**: the gate needs the official source PDFs —
deliberately untracked (`*.pdf` is gitignored), pinned by sha256 so a swapped
file fails loudly — and the regenerable `build/` tree, neither of which exists
on a hosted runner. The workflow asserts its own coverage table against
`SELF_TEST_MODULES` so a new module cannot be quietly uncovered, and nothing
in it is skipped or `continue-on-error`. The gate stays operator-run;
`local-runners/` is where its launchers and reports land.

## Documentation map

Each fact lives in exactly one document (the table is owned by `GOAL.md`;
this is the working copy):

| Document | Owns | Updated |
| --- | --- | --- |
| `README.md` (this file) | the process end-to-end: method, module map, how to verify | when the process changes |
| `STATUS.md` | **all measured numbers**: gate verdicts, assertion counts, findings tally, CI state | in the same commit as any change that moves a number |
| `GOAL.md` | objective, method, constraints, judgement calls | when the objective changes |
| `review-findings.json` | the defect ledger — scope of record | as findings resolve |
| `BLOCKER-PLAN.md`, `HANDOFF.md` | frozen historical records | never |

The rule that keeps this honest: **a commit that changes a number updates
`STATUS.md` in the same commit**, and no measured status number appears
anywhere else — including here. Stale-number drift across five documents is
how the previous state happened.

## Non-goals

- **No SVG page backgrounds.** Pixel-exact but static; a growable list cannot
  live inside one. That was the first solution and it is why we are here.
- **No raster references, ever.** Not as a gate, not as a diagnostic, not as a
  page background.
- **No per-form hand tuning.** A fix belongs in the algorithm or in the form's
  extracted data, never in a special case keyed on form code.
