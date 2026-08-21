# Handoff — formgen, PDF-native BIR form conversion

> **Frozen historical record as of 2026-07-30.** Kept as written; every number
> below is stale by design. Current measured state lives in `STATUS.md`, the
> process in `README.md`, the objective in `GOAL.md`.

Written 2026-07-30. Everything below was measured, not recalled. Where something
is unverified, it says so.

## How to use this handoff

**Start the session in the worktree, not the main checkout.** Everything below
assumes that; `main` is a different objective and must stay clean.

```sh
cd /Volumes/goldcoders/reverse-engineer-ebir-forms/bir/.claude/worktrees/pdf-native-extraction
claude
```

Then give the agent this, verbatim:

> Read `tools/formgen/HANDOFF.md`, then `tools/formgen/GOAL.md`. Run
> `python3 tools/formgen/gate.py --skip-regenerate` to see where things stand.
> Do the next increment from the handoff's `## Next increment`. Obey
> `## Constraints` in GOAL.md — they outrank finishing. Report what you measured,
> including anything that got worse.

Or, to have it drive itself without prompting: `/goal`. That reads
`.claude/GOAL.md`, checks the done-condition, does one increment, and re-arms.
It only asks a question when the gate passes.

### The three rules to state up front, every time

An agent that does not know these will reproduce mistakes already paid for:

1. **One agent per file.** If you fan out, assign ownership explicitly and tell
   each agent to stop and report rather than edit a file it does not own.
2. **A change to `extract.py` must name its caller in the same increment.**
3. **A check that cannot be evaluated is a failure, never a pass.**

### Reading order, and why

`HANDOFF.md` (state) → `GOAL.md` (objective + constraints + judgement calls) →
`BLOCKER-PLAN.md` (mechanisms) → `review-findings.json` (scope of record).

Read GOAL.md's `## Judgement calls already made` **before changing anything**.
Several of those decisions look wrong to someone reading only the symptom —
`crispEdges` is the clearest: it reads like the right choice for a page of thin
rules, and it *deletes* them.

### Verifying, not trusting

- `python3 tools/formgen/gate.py` is the only thing that decides whether the work
  is done. Never answer that from memory or from what an agent reported.
- `--skip-regenerate` scores the tree as it stands; it is fast and it is **not**
  the done-condition. It has already reported green off a stale audit.
- Every module has `--self-test`. `lattice.py` and `fonts.py` need
  `--ir build/ir/2551q-2018.ir.json`.
- A full gate takes ~45 minutes. Run it in the background and do read-only
  diagnosis meanwhile — but never run `batch.py` or `audit.py` concurrently with
  it, and never kill it without recording why.

### If you change the pipeline mid-run

A running `gate.py` imported its modules at process start. Editing `extract.py`
under it produces numbers from the *old* code. Kill it and restart rather than
reading a stale result as fact.

## Where the work is

| | |
| --- | --- |
| Worktree | `/Volumes/goldcoders/reverse-engineer-ebir-forms/bir/.claude/worktrees/pdf-native-extraction` |
| Branch | `gol/pdf-native-form-extraction` |
| Starting HEAD for the current increment | `cf0ee1b` |
| Current increment | Measurement-integrity fixes and overlap triage; inspect `git status` for whether they have been committed |
| Main checkout | **clean** — `/Volumes/goldcoders/reverse-engineer-ebir-forms/bir`, untouched |
| Nothing pushed | branch is local only |

Ten commits preceded the current increment, oldest first:

```
8722df8  convert all 51 BIR forms from PDF content streams
76c6c7c  declare @fontsource/tinos beside the other shipped faces
1771d01  fillable fields, printable guides, and band coverage
ae66231  metric proof, degenerate cells, tracked-file guard, revisions
838cfd8  blocker remediation plan from the 51-form review
b2bd2e9  goal, done-condition and the 138-finding ledger
17dedc7  gate.py, the done-condition as one command
8f19c58  the gate was green on five forms it never measured
d10e026  hash artwork as it appears on paper, over white
cf0ee1b  docs(formgen): how to use the handoff
```

⚠️ `b2bd2e9` is mislabelled. A careless `git add -A` swept 3,313 lines of
extractor work (`extract.py`, `lattice.py`, `guides.py` — the whole of "Round 1")
into what its message describes as a docs commit. Nothing is lost; the message
is simply wrong about its contents.

## Run it

```sh
cd /Volumes/goldcoders/reverse-engineer-ebir-forms/bir/.claude/worktrees/pdf-native-extraction
python3 tools/formgen/gate.py                      # the done-condition, ~45 min
python3 tools/formgen/gate.py --skip-regenerate    # score the tree as it stands, fast
python3 tools/formgen/gate.py --only assertions    # refresh + score assertions only
cd forms && python3 -m http.server 4190            # browse the output
```

`--skip-regenerate` and `--only` are **not** the done-condition and say so.

## Current status: the gate FAILS, 3 of 10

Measured on a full regenerate from the current increment:

```
PASS  self-tests     8 modules
PASS  conversion     51/51
PASS  rules          clean on 51/51
PASS  paper          exact on 51/51
FAIL  artwork        images_missing=1 on 1701MS
PASS  text           clean on 51/51
FAIL  assertions     5 of 8 fail
FAIL  findings       52/84 blocker+major unresolved
PASS  tracked-files  no tracked deletion
PASS  determinism    byte-identical
```

### The eight assertions, individually

```
inputs_over_printed_text         12/51 hold   ← 39 forms / 227 cells fail
comb_slots_match_printed         27/51 hold   ← 24 forms / 255 cells fail
money_boxes_have_inputs          47/51 hold   1601EQ, 1801, 2200A, 2200P
rules_below_guide_cut            51/51 hold
run_colour_matches_ir            51/51 hold
reflow_rate_without_description  50/51 hold   2551M
image_transform_applied          50/51 hold   1702Q
no_invented_codepoints           51/51 hold
```

### The findings ledger

`tools/formgen/review-findings.json`, 138 findings from a 51-form visual review.
**137 of them were invisible to the numeric audit.**

```
blocker    5 fixed / 16 open      ← gating
major     27 fixed / 36 open      ← gating
minor      4 fixed / 36 open      not gating
cosmetic   2 fixed / 12 open      not gating
```

## What to read, in this order

1. `tools/formgen/GOAL.md` — objective, done-condition, constraints, judgement
   calls already made. **Read the judgement calls before changing anything**;
   several look wrong to someone reading only the symptom.
2. `tools/formgen/BLOCKER-PLAN.md` — the nine root causes with verified
   mechanisms.
3. `tools/formgen/README.md` — why the pipeline is vector-only, and why raster
   pixel-diffing was abandoned after 273 commits.
4. `tools/formgen/review-findings.json` — the scope of record.

## The pipeline

```
pinned PDF ─extract.py─► IR ─┬─lattice.py─► box model (cells, combs, growable bands)
                             ├─guides.py──► which regions are reference material
                             ├─fonts.py───► CSS face per run + per-glyph advance proof
                             └─emit.py────► index.html + guide.html
                                              │  Chromium print-to-PDF
                                              └─► extract.py (same extractor) ─► verify.py IR-vs-IR
batch.py  drives all 51        audit.py  corpus sweep + the 8 assertions
gate.py   the done-condition   index_page.py  forms/index.html
```

Output is `forms/<CODE>-<REV>/{index.html, guide.html, form.css, guide.css,
provenance.json}` with `base.css`, `fonts/` and `assets/` shared at the root.
`build/` is gitignored regenerable intermediates.

## What is done and measured

- **51 forms, 110 pages** converted from pinned PDFs. Four paper sizes including
  one landscape sheet, each from its own MediaBox.
- **Fonts metric-proven per glyph**: Arial→Arimo max 0.0077pt, Times New
  Roman→Tinos max 0.0048pt over 9,508 glyphs, Arial Narrow→Arimo at
  `scaleX(0.820047)` max 0.0058pt.
- **Fillable**: 10,212 field cells carry inputs; combs type per-slot on measured
  centres.
- **Printable guides**: 29 bundles, all with `@page` from their form's paper.
- **39 growable bands** driven at 1 row / capacity / capacity+4.
- **Guide split** freed 6.56M pt² without moving a rule on any form.

## Overlap triage — measured 2026-07-31

The 42-form headline was not 42 separate defects. Two audit-oracle errors first
made 13 cells look dirty when no live input was over ink:

- five trailing page cells absorbed inputs from the inert growable-band
  `<template>` that followed them;
- eight malformed comb slots extended outside their `overflow:hidden` parent,
  and the audit counted collisions in the clipped-away portion.

`audit.py` now stops at `<template>` and clips slot boxes to the live parent.
Its self-test proves both cases and also proves that a collision with a run owned
by the same lattice cell still fails. `gate.py --only assertions` now refreshes
that assertion audit into a temporary report and publishes it atomically; a
failed refresh or coverage below 51 forms fails closed instead of scoring stale
data. A full gate then measured:

```
inputs_over_printed_text   42 forms / 240 cells
                         → 39 forms / 227 cells

remaining 227 cells       = 194 comb + 33 plain
comb cells                = 151 partly inked + 43 every-slot-inked
visible input boxes       = 476 actually over printed glyph boxes
```

Ownership is diagnostic, never an exemption. Of the 194 comb cells, 184 hit only
their own assigned run, five hit foreign runs, and five hit both. Some own-run
cases are plainly unsafe live fields: 1606 `p2c135` puts both inputs over
`TRANSACTION`; 1600WP `p1c0` puts all three over header text; 1700 `p1c21`
puts five inputs over five printed zeroes.

The overlap and comb problems are coupled. The independent printed-compartment
oracle disagrees with the lattice on 29 of those 194 combs across 12 forms, every
one an undercount. Do not add a slot-input mask on top of geometry whose slot
count is already wrong.

## Next increment

**Referee `comb_slots_match_printed` before changing `emit.py`.** The corrected
audit reports 255 mismatching comb cells across 24 forms. Build the third check
described below, decide which oracle is wrong, and repair the general lattice
algorithm. No form-code exceptions and no raster result in the pipeline; a
rendered crop may be used only for human inspection.

After compartment truth is stable, the first conservative emission slice is the
124 oracle-agreeing, own-run, partially inked combs: keep every physical
compartment, omit inputs only from inked slots, preserve physical slot indexes,
and make keyboard/paste plus growable-band cloning skip fixed slots. The 43
fully inked combs, all foreign-run collisions, and the 33 plain fields remain
separate failures until their geometry is decided.

Then: `money_boxes_have_inputs` (4 forms), the three single-form failures, then
the 52 unresolved gating findings.

## Open questions someone must actually decide

1. **The comb-oracle dispute is unresolved.** `lattice.py`'s own measurement says
   merged combs went 471 → 13. An independent oracle over raw PDF drawing ops
   says **204 genuine residual merges across 13 forms**. `audit.py`'s corrected
   assertion is a *third* independent measurement and says 255 cells across 24
   forms fail. Nobody has
   refereed this. Do not pick a side by preference — build a check that decides
   it, ideally by counting compartments visible in a rendered crop. A comb whose
   slot count is wrong puts a typed digit on top of a divider bar.

2. **32 findings are marked resolved on the implementing agents' own word.** I
   have not independently re-verified them.

3. **No human has looked at the current forms.** The visual review that found all
   138 defects predates Round 2, which changed how every path, image and input is
   emitted. The gate cannot see what that review saw. **Round 4 (re-review) is
   unstarted and is the single highest-value remaining task.**

## Deferred, deliberately

- **9 of the official 51 forms have no source PDF**: 1600, 1601-E, 1601-F, 1602,
  1603, 1604-CF, 1704, 2000, 2200AN. Drop them in `~/Downloads/forms` and one
  `batch.py` run adds them. **Do not download them without asking.**
  (Our 51 ≠ BIR's 51: we have 42 of theirs, plus 3 attachment sheets and 13
  `extra/` forms outside the package. See GOAL.md.)
- **App integration.** Field ids are stable and deterministic but nothing binds
  them to the Rust `RenderEnvelope` or `assets/form-renderer`. A project, not a
  cleanup.
- **Type3 embedding.** Chromium embeds the bundled WOFF2 as Type3, so the differ
  cannot read back a font *family*. Advances, positions and sizes are verified.
  Static instances would fix it. Not gating.

## Process rules earned the hard way

- **One agent per file.** Two agents on `emit.py` concurrently cost a day and
  produced a regression the wrong agent was blamed for.
- **A change to `extract.py` must name its caller in the same increment.**
  `asset_for_xref()` shipped with no caller and regressed artwork 51/51 → 32/51.
- **A check that cannot be evaluated is a failure.** `gate.py` enforces this
  because it was itself green on five forms it never measured: `verify.py`
  short-circuits on a paper mismatch and returns zeros, and those zeros read as
  clean.
- **Never widen a tolerance, never special-case on form code, never rasterise in
  the pipeline.** Rasterising is permitted only so a human can look.
- **Report a cost; never trade it.** A partial objective with a clean tree beats
  a complete one that broke something.

## Mistakes made here, so they are not repeated

- The gate was green on five unmeasured forms — the tool built to prevent that
  failure had it.
- `guides.py` never claimed `paths`; they arrived with IR schema 2 after that
  module was written. 0605 page 2 relocated everything and kept 532 stroked
  dashes.
- Artwork hashed raw samples, so a barcode at exactly the right bbox from exactly
  the right file counted as missing. Under a transparent pixel the source keeps
  its base RGB while Chromium flattens against the page.
- "Round 1 debt: rules_extra 399" was never real — stale HTML measured against
  new IR.
- `@fontsource/tinos` was first installed into the **main checkout**, dirtying
  `main`. It belongs on this branch in `packages/form-renderer/package.json`
  beside the other shipped faces, which is where it is now.
