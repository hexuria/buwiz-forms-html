# Blocker remediation plan

> **Frozen historical record as of 2026-07-30.** Kept as written; every number
> below is stale by design. Current measured state lives in `STATUS.md`, the
> process in `README.md`, the objective in `GOAL.md`; findings resolve in
> `review-findings.json`.

Derived from the 51-form visual review (138 findings, 137 of them invisible to
the numeric audit). Every mechanism below was verified directly against the
source PDFs and the generated artefacts, not taken from a report.

The 21 blockers and 63 majors are **not 84 problems**. They are **nine root
causes**. Fixing the nine fixes the 84.

Ordering principle: causes that other causes depend on go first. C1 (dingbats)
must precede C4 (checkbox inputs), because the missing checkbox inputs are
caused by a dropped glyph rendering as `'?'` inside the box.

---

## C1 — Dropped glyphs become `'?'` and poison cell classification

**Severity: blocker (cascading).** Affects 2200S, 2200T, 1707, and every form
with Wingdings/Symbol dingbats.

**Mechanism, verified.** 2200S page 1 carries the text run `'?        '` at
x 347.47–373.87, y 107.0. That run is a Wingdings checkbox glyph that extraction
turned into `?`. It overlaps the item-2 "Yes" checkbox at x 358.7–372.0, so
`lattice.py` sees a cell containing text, classifies it `label`, and `emit.py`
never gives it an input. The taxpayer can tick "No" on an amended return but not
"Yes".

**Fix.** Land the substitution table `fonts.py` now produces (already built, from
the previous round) and, critically, stop extraction emitting a placeholder
`?`/`U+0000` for an unmapped codepoint — an unrepresentable glyph must be carried
as its real codepoint with a flag, never as a character that looks like content.

**Files:** `extract.py` (codepoint fidelity), `emit.py` (apply substitution).
**Verify:** no text run in any IR contains `?` where the source codepoint was not
`U+003F`. 2200S's three checkboxes gain inputs as a consequence, not by special
casing.

---

## C2 — Soft-masked images render as solid black rectangles

**Severity: blocker.** 1604C, 1604E, 1700, 1701, 1701MS, 1702EX, 1702MX, 1702Q,
1702RT.

**Mechanism, verified.** 1604C xrefs 43 and 45 and 1604E xrefs 37 and 39 each
carry `/SMask`. The base RGB stream is a flat black fill — 39 compressed bytes on
1604E xref 39 — and the alpha channel is what shapes it into glyphs. We call
`doc.extract_image(xref)`, which returns the base image only, so the mask is
discarded and the flat fill is painted at full opacity. On 1604C that
obliterates the pre-printed "BCS/" and "Item:" labels; on 1702EX/MX/Q/RT it
paints a black bar across a "Year Ended" entry box.

**Fix.** Composite the soft mask at extraction. `fitz.Pixmap(doc, xref)` plus the
SMask pixmap yields RGBA; emit PNG with alpha. Record in the IR that the asset is
masked, and hash the *composited* pixels so `pixel_sha256` still answers "same
picture".

**Files:** `extract.py`, `batch.py` (`extract_images` writes the asset).
**Verify:** the flagged regions show the official's text, not a block. Re-hash:
artwork must stay 51/51 with 0 placement violations.

---

## C3 — Image placement discards the transform matrix

**Severity: blocker.** 2550M (seal upside-down); a latent risk on every form.

**Mechanism, verified.** 2550M xref 51 has
`transform = (40.8, 0.0, -0.0, -36.72, 30.48, 74.64)`. The negative `d` is a
vertical flip. Every other image in the corpus has positive `d`. The IR records
only `{x0,y0,x1,y1}`, so the flip is lost and the seal renders inverted — rim
lettering reads bottom-to-top.

**Fix.** Record the full 6-element matrix from
`page.get_image_info(xrefs=True)["transform"]` and apply it in the SVG image
element. Do not special-case the flip: carry the matrix, which also covers
rotation and skew if any form ever uses them.

**Files:** `extract.py`, `emit.py`.
**Verify:** assert corpus-wide that any image whose matrix is not a positive-
diagonal scale is emitted with a matching transform. 2550M's seal reads
correctly.

---

## C4 — `kind="mixed"` cells get no input

**Severity: blocker.** 2000-DST (entire page-1 money grid), 2200A (Part III
items 16–24 and Part IV 25–27), 2200P (Part III), 1801 item 24, 2316 items 23–24,
1702EX item 18.

**Mechanism, verified.** 2000-DST's money cells `p1c49`…`p1c63` are
`kind="mixed", comb=yes, runs=1`. They *are* combs — they just also hold the
pre-printed decimal point. `emit.py` emits inputs only for `kind=="field"`, so
every money row on the sheet is unfillable, including the headline payable.

**Fix.** A cell carrying a `comb` is fillable regardless of any pre-printed text
inside it. The `.` and `%` are decoration within the field, not a label. Widen
the field predicate to `kind in {"field","mixed"} or cell.comb`, and treat the
pre-printed run as non-editable content the input renders behind.

**Files:** `emit.py` (predicate), possibly `lattice.py` if `mixed` should split.
**Verify:** fill every input on 2000-DST, 2200A, 2200P, 2316; every printed money
box receives a character. Count inputs before/after per form.

---

## C5 — Comb slots merge across heavy group dividers

**Severity: blocker.** 1707 (TIN and every money comb), 1800 item 5 middle
triplet, 1801 items 5/11 and page-2 TIN, 2200C item 1 MM and YYYY groups, 2316
items 3/7/8/12/16.

**Mechanism, verified.** 1707 `p2c5` reports `cells=11, dividers=10,
divider_thicknesses_pt=[0.48]` with slot widths
`[14.16, 14.28, **28.58**, 14.28, **28.92**, 14.28, **28.8**, 14.4, 14.4, 14.43,
14.4]`. Fourteen printed cells became eleven slots, three of them double width,
because the 1.44pt group separators were not counted as comb dividers — they pass
the box-border test (supported at both ends) and so are classified as borders.
A typed character then centres on top of the black separator bar.

**Fix.** A vertical rule inside a comb band's x-range and y-range is a slot
boundary regardless of thickness or end support. Thickness distinguishes a
character divider from a group separator; it does not decide whether a boundary
exists. Keep both in `divider_thicknesses_pt` so the distinction stays visible.

**Files:** `lattice.py`.
**Verify:** every comb's slot count equals its printed cell count. Corpus check:
34 comb groups were reported merged; that must reach 0. 2551Q's pinned comb
numbers must not move — its 3-3-3-5 TIN grouping is correct today.

---

## C6 — Reference tables emitted as editable fields

**Severity: blocker.** 1700 page 2 (TABLE 1/TABLE 2 tax brackets), 1701A page 2,
1701Q page 2, 1701MS page 2, 2000-DST page 2, 2200A pages 2–3, 2200P page 2,
1801 page 2, 2553 page 1.

**Mechanism.** These are pre-printed statutory rate tables that `guides.py` did
not classify as reference material, so their cells reached `lattice.py` as ordinary
cells and `emit.py` gave them inputs. On 1700 a taxpayer can type over "Not over
P 250,000". Confirmed live: the DOM has
`<div class="c f" data-field-kind="text" id="p2c131">` with an editable child and
no `readonly`.

**Fix, two parts.**
1. `guides.py` must recognise a rate/reference table that is not introduced by one
   of the three strict markers. The structural signal is already there and is the
   one that has worked all along: a contiguous band whose cells hold pre-printed
   text and contain no comb and no empty enclosed cell is reference material.
   Widen detection using that test, not new keywords — keywords already produced
   a false positive on "Add: Penalties".
2. Independently, `emit.py` must not make a cell editable when its geometry is
   entirely occupied by pre-printed text. That is the belt to C6's braces, and it
   protects forms whose reference tables are never relocated.

**Files:** `guides.py`, `emit.py`.
**Verify:** no input on any form overlaps a pre-printed text run's bbox. This is a
corpus-wide assertion worth adding permanently to `audit.py`.

---

## C7 — Cutting a guide region leaves an orphaned frame

**Severity: blocker (2000-OT) / major.** 1600-PT p2, 1600-VT p2, 2000-OT p2,
2550M p3.

**Mechanism, verified.** The rule "a straddling element belongs to the form" was
chosen so a cut could never *lose* a rule. It has the opposite failure: the
relocated table's grey title band and its full-height outer verticals stay on the
form, drawing an empty three-sided box down two-thirds of the page. 1600-PT keeps
`v85` and `v148`, each 1.44 × 461.33pt, with nothing between them.

**Fix.** Clip straddlers at the cut instead of awarding them wholesale. The
portion above the cut stays on the form; the portion below goes to the guide.
That is exact, loses nothing, and needs no judgement about intent.

**Files:** `guides.py` (emit clipped geometry for both sides), `emit.py`.
**Verify:** no rule on a form page extends below its cut. Form rules must stay
100% on 51/51 — a clipped rule is still a rule, so the differ must be given the
clipped reference.

---

## C8 — Hidden white text is published

**Severity: major, but it is a disclosure defect.** 1600-PT p2, 1600-VT p2.

**Mechanism, verified.** Both sheets carry 25 text runs at `color 16777215`
(0xFFFFFF) — BIR internal reviewer initials, invisible on white paper in the
official. Our guide renders them **black**, so content the source deliberately
hid is now legible and reads as ATC data.

**Fix.** Carry the run's colour through to the CSS. We already extract `color`
per run and then ignore it. Emitting the real colour fixes this by construction
and is more correct generally. Do **not** drop white runs: they are in the
document, and a future form may use white text over a dark band legitimately.

**Files:** `emit.py`. `extract.py` already records `color`.
**Verify:** the six initials rows disappear from both guides. Assert no emitted
run's colour differs from the IR's.

---

## C9 — Guide reflow decouples tax rates from their descriptions

**Severity: major, and the only *correctness* hazard in the list.** 1600-PT,
1600-VT, 2000-OT, 2550M, 2551M, 2551Q.

**Mechanism.** The reflow lays out one row per printed *line*, so a two-line ATC
description leaves its rate and code stranded on a blank-description row.
1600-PT's guide shows "Franchise Tax on radio & TV broadcasting companies whose
annual gross receipts do not exceed P10M &" with no rate, then a row containing
only "3% | WB 050". A reader can attach a rate to the wrong nature of payment.
2551M is worse — its rate table is flattened into running prose, destroying the
column relationship entirely.

**Fix.** Reflow must reconstruct table *rows*, not lines: group runs by the
lattice row they fall in, and keep a row's cells together. The lattice already
knows the row structure; the reflow is currently ignoring it and working from
text-run y alone.

**Files:** `emit.py` (reflow), reading `lattice.py`'s row data.
**Verify:** for each relocated table, every row that has a rate also has a
non-empty description. Assert no row has an empty first cell and a non-empty rate
cell — that pattern is the defect signature and is machine-checkable.

---

## Also in scope, smaller

| # | Defect | Cause | Files |
|---|---|---|---|
| S1 | ► pointer triangles render as grey hairlines (0605, 1600WP, 2550M, 2551M, 2553) | `extract.py` handles only axis-aligned `re` and `l`; a diagonal filled path is flattened to horizontals and the fill dropped | `extract.py`, `emit.py` |
| S2 | Pre-printed decimal points missing (0605, 2551M, 2553) | small `fs` filled shapes ~1.68 × 1.5pt dropped by the rule/fill classifier | `extract.py` |
| S3 | Blank orphan pages (2200P p3, 2550M p3) | page emitted after all its content was relocated | `emit.py` |
| S4 | 2550M p4 guidelines not relocated | `guides.py` detection missed a whole guideline page | `guides.py` |
| S5 | Leading-whitespace runs mis-positioned (2550-DS, 2550Q, 1707A, 2200P) | run origin taken before leading spaces | `emit.py` |
| S6 | Justified text smeared as letter-spacing (2553) | word-spacing distributed across all glyphs | `fonts.py` |
| S7 | The 3 remaining unmatched runs | confirmed genuine render defects | `emit.py` |

---

## Execution order and file ownership

Concurrent agents must never share a file. Two rounds, serialised by that
constraint:

**Round 1 — extraction truth.** Nothing downstream can be right while the IR is
wrong.

| Agent | Owns | Causes |
|---|---|---|
| A | `extract.py` | C2, C3, S1, S2, and C1's codepoint half |
| B | `lattice.py` | C5 |
| C | `guides.py` | C6 part 1, C7, S4 |

**Round 2 — emission**, after Round 1's IR and layout land.

| Agent | Owns | Causes |
|---|---|---|
| D | `emit.py` | C1 apply, C4, C6 part 2, C8, C9, S3, S5, S7 |
| E | `fonts.py` | S6 |
| F | `audit.py` | the new corpus assertions below |

`emit.py` has one owner. That is deliberate: two agents editing it concurrently
already cost us a day.

**Round 3 — regenerate, re-audit, and re-review** the 40 forms that had findings.

## New permanent assertions

The audit passed 137 real defects. These make that class of blindness
machine-detectable, and belong in `audit.py`:

1. No input overlaps a pre-printed text run's bbox.
2. Every comb's slot count equals its printed cell count.
3. Every printed money box on a form has an input.
4. No rule on a form page extends below that page's guide cut.
5. No emitted run's colour differs from the IR's.
6. No relocated table row has an empty description with a non-empty rate.
7. Every image whose transform is not a positive-diagonal scale is emitted with a
   matching transform.
8. No IR text run contains `?` where the source codepoint was not `U+003F`.

## Non-negotiable throughout

- rules 100% on 51/51 and paper exact 51/51 after every round. Report a cost;
  never trade it.
- Never widen a `verify.py` tolerance. Never special-case on form code.
- The pipeline never rasterises. Rasterising is permitted only for human review.
- Deterministic: same input, byte-identical output.
