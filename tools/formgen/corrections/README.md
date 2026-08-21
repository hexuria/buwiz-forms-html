# Stage 2 — correction records

    STAGE 1  GENERATE   pinned PDF -> IR -> lattice -> emit -> HTML   (forms/, batch-versioned)
    STAGE 2  CORRECT    forms-corrected/ = copy(batch vN) + declared correction records
    STAGE 3  MAP        fields -> eBIRForms XML payload keys, bound to forms-corrected/

This directory holds stage 2's **ledger**: the declared corrections, their
evidence, and the schema they are checked against. The applier is
[`../correct.py`](../correct.py). The independent fidelity producer is
[`../corrected_fidelity.py`](../corrected_fidelity.py). Passing
`validate_records.py` means the pair is well formed, never that the
correction is true.

## What a record is

A record is **data, not a patch**. It says which form, which printed box, what
changes, why, on whose authority, what effect to expect, and who is allowed to
confirm that effect. It is applied to a byte-copy of a **named** stage-1 batch;
a form with no record is copied byte-identically. Full rationale, and the
reasoning that got here, live in [`../ARCHITECTURE.md`](../ARCHITECTURE.md)
§"What belongs in which stage" and §"Batch-versioned immutability".

The dividing line, which decides whether something belongs here at all:

> **Stage 2 is for facts the source CANNOT tell us. Stage 1 is for us
> misreading a source that is correct.**

A producer bug routed through here buys speed now and pays forever: 53 forms of
hand-maintained corrections re-verified on every regeneration, while the bug
still ships to form 54. Anything proposed for this ledger must first be shown
*not* to be a stage-1 row in [`../PLAN.md`](../PLAN.md).

## What authority means

Authority is **who says the artwork is out of date**, in a form a reviewer can
check. Not the agent's judgement, not a plausible-sounding regulation number.

Ranked, best first:

1. a BIR issuance (revenue regulation, memorandum circular, release note) that
   is identifiable from what is in this repository;
2. a declaration harvested from BIR's own shipped eBIRForms client —
   `rules/forms/<form>/fields.json`, with the `field_key`, the constraint, the
   `confidence` and the `source_refs` quoted;
3. nothing. Then the record says so.

**A fabricated citation is the worst defect this project could ship.** If the
specific issuance cannot be identified in-repo, the record writes exactly:

    BIR eBIRForms HTA runtime declaration (harvested), regulation not identified in-repo

and that string is what the divergence report prints. An honest gap stays
visible until someone supplies the real issuance. `validate_records.py` refuses
a record whose `authority.regulation_reference` is blank — the gap must be
stated, not left empty.

## The four binding rules

Restated only as headlines; they are settled in
[`../ARCHITECTURE.md`](../ARCHITECTURE.md) §"Rules the user set" and
§"Batch-versioned immutability", which is the text that governs.

1. **A batch is immutable once a sighted gate scores it.** A generator fix
   produces the NEXT batch; the applier never mutates `forms/`.
2. **One generator version per batch.** The applier records WHICH batch it
   consumed.
3. **Stage 2 consumes a named batch.** Per form: byte-copy, then apply that
   form's records. **No record → byte-identical copy.**
4. **A correction never hides a divergence.** The corrected tree stays
   checkable against the official PDF, and a corrected field still registers as
   divergent — reported as `diverges by declared override <id>, authorised by
   <authority>`, never silently equal. An override must never become the way to
   silence an inconvenient check.

Rule 4 is the one the project's integrity rests on, so the schema is built so
that violating it fails validation rather than passing quietly: `never_hidden`
is a `const true`, the divergence text must name the record and quote its
authority verbatim, every record must name the checks that will report it, and
no record may reach `verified` while any of those checks is unproven.

Two more constraints inherited from the plan, and enforced here:

- **The verifier must share no producer with the correction** — re-derive from
  the pinned PDF and from the corrected markup, never from the `build/layout`
  or IR the correction just moved, and never from the applier's own manifest
  ([`../PLAN.md`](../PLAN.md) risk R3; the `?debug=fields` overlay reported
  233/233 OK on a visibly wrong page).
- **A correction that cannot state its effect in advance cannot land.**
  `expected_effect` is declared before the applier runs, and a verifier is held
  to it.

## Files

Two files describe one correction, and the split is deliberate. `../correct.py`
reads the ledger entries at the root of this directory and its record schema is
**closed** — an unknown key is a refusal, so no record can smuggle in a
`suppress` or an `allowlist`. That same closure means a ledger entry cannot
carry structured evidence, so the evidence lives beside it under `evidence/`,
where the applier's loader (files only, root only) never sees it.

| File | What it is |
| --- | --- |
| `C01-2550m-tin-branch-code.json` … `C07-1604cf-tin-branch-code.json` | Ledger entries the applier reads. Seven records: C01 re-anchored against HEAD `98ca03c3`; C02–C07 authored for the remaining census sites. |
| `evidence/C0N-evidence.json` | Per-record evidence: every measurement, its source, and how to re-derive it. |
| `schema/correction-record.schema.json` | The evidence-record shape. Kept out of the ledger root so the applier does not try to apply it. |
| `validate_records.py` | Checks each evidence record against the schema and proves it still agrees with its ledger entry. **Not the applier, not the verifier.** Passing means well formed, never means true. |
| `evidence/measure_tin_branch_census.py` | Measures, from the pinned PDFs alone, what each bundle PRINTS for its TIN comb chain. Evidence tooling; nothing in the pipeline consumes it. |
| `evidence/tin-branch-census-20260808.json` | That script's output for all 53 bundles, 2026-08-08. |

```sh
python3 tools/formgen/corrections/validate_records.py
python3 tools/formgen/corrections/evidence/measure_tin_branch_census.py \
    --forms-root ~/Downloads/forms --repo .
```

If the two files ever disagree on the form, the id, or the authority wording,
`validate_records.py` fails: reviewed evidence that no longer describes what is
actually applied is the same defect as a census pin that no longer matches its
producer.

The census script needs the pinned official PDFs, which are never committed; it
locates each one by the sha256 already recorded in `build/layout/*.layout.json`
and refuses to measure a file that does not match.

## The ledger

| ID | Form | Change | Authority | Status |
| --- | --- | --- | --- | --- |
| C01 | `2550m-2007` | whole TIN strip reflowed to even 3-3-3-5; branch **locked `00000`** (official pre-prints `000`) | harvested HTA `frm2550m:txtBranchCode` ml=5; regulation not identified in-repo | `verified` (re-anchored 2026-08-20 to `0c1def60`) |
| C02 | `0605-1999` | whole TIN strip reflowed to even 3-3-3-5; branch **5 editable cells** (official box blank) | harvested HTA `frm0605:txtBranchCode` ml=5 (`0605-v2003`); regulation not identified in-repo | `verified` (re-anchored 2026-08-20 to `0c1def60`) |
| C03 | `2551m-2002` | whole TIN strip reflowed to even 3-3-3-5; branch **5 editable cells** (official box blank) | user rule 2026-08-15 (3-3-3-5); no harvested fields.json; regulation not identified in-repo | `verified` (re-anchored 2026-08-20 to `0c1def60`) |
| C04 | `extra/2553-1999` | whole TIN strip reflowed to even 3-3-3-5; branch **5 editable cells** (official box blank) | harvested HTA `frm2553:txtBranchCode` ml=5; regulation not identified in-repo | `verified` (re-anchored 2026-08-20 to `0c1def60`) |
| C05 | `extra/1600wp-2010` item 5 primary | whole TIN strip reflowed to even 3-3-3-5; branch **5 editable cells** (4 official compartments, none pre-printed) | harvested HTA `frm1600WP:txtBranchCode` ml=5; regulation not identified in-repo | `verified` (re-anchored 2026-08-20 to `0c1def60`) |
| C06 | `extra/1600wp-2010` agent TIN | whole TIN strip reflowed to even 3-3-3-5; branch **5 editable cells** (4 official compartments, no ink inside) | user rule 2026-08-15; no agent-branch field_key; regulation not identified in-repo | `verified` (re-anchored 2026-08-20 to `0c1def60`) |
| C07 | `extra/1604cf-2008` | whole TIN strip reflowed to even 3-3-3-5; branch **locked `00000`** (official pre-prints `000` in 3 of 4) | user rule 2026-08-15; no harvested fields.json; regulation not identified in-repo | `verified` (re-anchored 2026-08-20 to `0c1def60`) |

### The branch-lock rule

The branch group is locked to `00000` **only where the official artwork already
pre-prints `000` in the branch box** — C01 and C07, and nowhere else. Where the
official box is blank, the five cells stay **editable**: C02, C03, C04, C05 and
C06 are payment and withholding sheets whose filer must be able to enter a real
branch code, and painting a locked `00000` over a blank official box would
silently credit every remittance to the head office. `lock_branch` in
`tools/formgen/review/reflow_tin_chain.py` carries the distinction, and it is
set from the pinned artwork's own ink, never from convenience.

### Charbox chrome (sitting 2026-08-17)

The even 3-3-3-5 split does **not** get to restyle the TIN as a grid of
full boxes. Official charboxes (2550M item 1 month, rule `v172`) put a
complete 0.72pt frame on the **outer** group only; interior ticks are
short hairs on the floor of the box (~33% of its height). Dash/gap cells
between TIN groups keep the dedicated SVG fill (`#c0c0c0` / `#808080` /
`#ffcc99` / `#e3e3e3` depending on the sheet) and a black frame, and they
stay non-tabbable. `reflow_tin_chain.py` paints that chrome after the
white knockout, because the knockout hides the official ticks and fills.

### Execution queue after this sitting

Do these in order. Do not start a later row until the earlier one has a
human verdict or an explicit skip.

| # | Where | What | Why it is that layer |
| --- | --- | --- | --- |
| **P0** | `gol/tin-stage2` / [PR #17](https://github.com/hexuria/buwiz-forms/pull/17) | TIN chrome restore (this section) + even 3-3-3-5 + lock rule | Stage 2 residue: the source prints 3 or 4 branch boxes. |
| **P2** | `gol/tin-stage3` PR #18 (stacked on #17) | Tab: 0605 17 then 18; 2550M page-2 first schedule row (horizontal walls). | Stage 1 lattice. TIN overlay is a later batch. |
| **P1+P1b** | `gol/tin-stage4` (this commit, stacked on #18) | Charbox hair ticks and X-squares stamp `maxlength="1"`; stay `type=text`. | Stage 1. Never a TIN record. |
| **P0b** | `gol/tin-stage5` | Named Stage 1 batch now current `main` `forms/` (`0c1def60`). C01–C07 re-anchored 2026-08-20. Sittings 2026-08-18/19. `corrected-tree` PASS. | Stage 2. Status `verified` on this tree. |
| **I0** | `gol/field-identity` | Durable identity catalog: 180 TIN-strip identities (C01–C07 seed plus 38 corpus strips). `p1cN` is a hint. | Not a mapper. |
| **M0** | `gol/tin-map` PR #27 | R1 TIN mapper: 163 harvested keys onto `name=` in `forms-corrected/` only. | Stage 3. R3/R7 stay closed. |
| **J2** | `gol/leftover-keys` | Leftover-key census: 8028 unique unclaimed keys, not joins. Windows dummy-Save: `HANDOFF-WINDOWS-EBIRFORMS.md`. | 2/3 seam. No harvest. |

Status on C01–C07 is `verified`. Find/replace re-anchored 2026-08-20 to
current `main` `forms/` (`0c1def60`) after tab-order reordered C01/C03/C04
cells. `correct.py --batch HEAD` + `--verify` PASS.
`corrected_fidelity.py` observed all 7 divergences.
`gate.py --only corrected-tree` PASS (7/7 sentences named).
The 2026-08-18/19 sittings remain the chrome/lock human approval.

Why C01 is genuinely stage 2: the 2007 artwork is **correct and out of date**.
It prints three compartments and pre-prints `000` in them; BIR's own client
declares `frm2550m:txtBranchCode` with `max_length: 5`. No rule derives "BIR
widened this after 2007" from 2007 ink. After the 2026-08-17 sitting the
correction reflows the **whole** TIN strip to even 3-3-3-5 inside that same
outer span (not five squeezed writable cells in the old last box), and locks
the branch as `00000` — a lock C01 earns because the 2007 sheet prints `000`
there, and one the blank-box forms C02–C06 do not.

**Measured while authoring it, and not what the briefing said:** 2550M is not
the only bundle printing `3+3+3+3`. Of 53 bundles, **39** print `3+3+3+5`,
**three** print `3+3+3+3` (2550M, 2551M 2002, 2553 1999), **one** prints
`3+3+3+4` (1600WP 2010), and **ten** are not measurable by that method and are
listed rather than guessed. 2553 declares `max_length: 5` too and therefore
carries the same defect; 2551M has no harvested `fields.json` in this checkout
and so has no in-repo authority at all; 1600WP prints four and declares five.
None of them is corrected by C01 — each needs its own record, evidence and
review. Nor is the declaration uniform: 1601C declares `max_length: 3` while
its artwork prints five compartments, so "the HTA always says 5" is false and
must not become a rule.

## Adding a record

1. Show it is not a stage-1 row in [`../PLAN.md`](../PLAN.md).
2. Measure the artwork from the pinned PDF, by a reader that is not
   `extract.py`. Quote coordinates.
3. Quote the authority verbatim from the file it lives in, or write the honest
   gap.
4. Declare `expected_effect` **before** anything is applied, and name the checks
   that must report the divergence.
5. `python3 tools/formgen/corrections/validate_records.py`.
6. The user reviews. `status` becomes `applied` when the applier writes
   `forms-corrected/` and the sitting is accepted. It stays off `verified`
   until an independent producer re-derives the effect and the named
   divergence checks are seen to fire (`proven: true`).

## What C01 does NOT yet have

`status` is `applied` (sitting 2026-08-18). It is not `verified`.

The applier was run once against `forms/` at HEAD `77987f8` into a scratch tree
outside the repository: the anchor matched, 371 of 372 files copied
byte-identically, one file changed, and all four declared effects were
re-derived from the bytes written. **That is an applier self-check, not
verification** — its own manifest says
`self_check_is_not_independent_verification: true`, and rule 4 does not accept
it. So no assertion in the evidence record is marked `held`.

What is still missing, and what `verified` would require:

- the audit run over a corrected tree, showing `comb_slots_match_printed` and
  `inputs_span_no_printed_divider` reporting the branch comb as an offender —
  `proven: false` on every divergence check until someone has SEEN that;
- the compartment count re-derived from the written markup by a parser that
  reads none of `emit.py`'s markers;
- the user's review.

Until then, no number in this directory describes a gate-measured tree, and a
number measured on a tree that was not written is exactly the failure this
project keeps finding.
