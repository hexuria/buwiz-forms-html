# Goal: PR #13 mergeable, fully documented, every known issue closed

Bring https://github.com/hexuria/buwiz-forms/pull/13 to a state where merging
it is a decision about scope, not a gamble about quality: the gate passes, CI
is green, the documentation lets a newcomer understand and safely change the
system, and every finding in the ledger is fixed or explained.

Supersedes the previous goal (integrity-only scope). The user has explicitly
pulled the defect backlog INTO scope: "fix all possible issues, gaps, edge
cases."

## Done when

```sh
python3 tools/formgen/gate.py     # exits 0 — all 12 checks
gh pr checks 13                   # every check green
```

**Coverage is part of done, and it counts CODES, not bundles.** Measured
2026-08-06 against bir.gov.ph/ebirforms:

    51 bundles on the index  -3 extra bundles (1701 ships main + attachment +
    conso, 1702MX ships main + attachment)  =  48 unique form codes

Those 48 overlap BIR's 51 by only **42**. We are missing **9** BIR forms
(1600, 1601-E, 1601-F, 1602, 1603, 1604-CF, 1704, 2000, 2200AN) and carry
**6** BIR does not list (0620, 1621, 1709, 2000-DST, 2316, 2550-DS), which the
user has asked to keep. 42+9=51 official, 42+6=48 ours.

Target: **BIR's 51 + the 6 extras = 57 codes.**

Three coincidences made this invisible for weeks: 51 bundles happens to equal
BIR's 51 forms, every count in the tooling counted bundles, and the 6 extras
exactly masked part of the 9-form hole. "51/51 converted, 100%" was true about
bundles and false about coverage. Any count that reports coverage must
therefore report unique codes and name its denominator. The user has authorised
downloading them. Any that BIR does not publish is recorded under `## Blocked`
with what was tried -- never substituted from a mirror or a third party.

Do NOT merge the PR. The user has said explicitly: land every form first,
then they review.

A check that cannot be evaluated is a failure. Never edit either command to
make it pass. The gate regenerates twice, audits, and runs the comb referee;
~60 min. CI runs the no-external-input subset on every push.

## What "done" decomposes into

The gate's four failing checks, and what each needs:

1. **artwork** — 1 image missing on 1701MS. Diagnose which placement and why;
   it predates the manifest work.
2. **assertions** — 5 of 8 fail:
   - `inputs_over_printed_text` (48 forms): two known populations — an input
     over its own field's pre-printed decoration (may be legitimate; the money
     "." renders behind inputs BY DESIGN) vs genuine overlap of form labels.
     Triage first; a principled narrowing of the assertion is allowed ONLY
     with the populations separated and counted, never to make numbers move.
   - `comb_slots_match_printed` (51 forms): adjudicated by the comb referee's
     19 mismatches. Fix the producer at fault per mismatch (lattice vs emit vs
     extract), not the assertion.
   - `money_boxes_have_inputs` (6 forms), `reflow_rate_without_description`
     (2551M — its ATC band has no ruled grid), `image_transform_applied`
     (1702Q).
3. **findings** — 52 of 84 blocker+major open in `review-findings.json`. Each
   must end `fixed` or `not-a-defect` with a non-empty resolution. Round 4
   (visual re-review of the 40 forms that had findings) closes the loop:
   screenshot ours, render the official at the same size, look at both.
4. **comb-referee** — 19 source/layout/emission mismatches → 0, via the
   producer fixes in (2).

Plus CI: the formgen job has NEVER run past its install step on a runner
(PyPI playwright pin fixed in 210044a). Later steps hold unknown latent
failures — fixture byte determinism across zlib builds is the known risk.
Iterate: push, watch, fix, until green.

Plus documentation (see below) — kept current as part of every increment,
not as a final pass.

## Documentation architecture (the deliverable, not a chore)

Verdict at time of writing: the METHOD is well documented; the verification
machinery is not, and status numbers are stale in three places.

Target structure — each fact lives in exactly ONE document:

| Document | Owns | Update trigger |
| --- | --- | --- |
| `README.md` | The process end-to-end: why vector not raster, module map incl. gate/validator/fixtures/manifest, how to run everything | when the process changes |
| `STATUS.md` | ALL volatile numbers: gate output, assertion counts, findings tally, CI state. The only doc allowed to contain a measured number | same commit as any change that moves a number |
| `GOAL.md` (this file) | Objective, method, constraints, judgement calls | when the objective changes |
| `review-findings.json` | The defect ledger — scope of record | as findings resolve |
| `BLOCKER-PLAN.md`, `HANDOFF.md` | Historical records; header must say so and point here | never (frozen) |

Rule that keeps it honest: **a commit that changes a number updates STATUS.md
in the same commit.** Stale-number drift across five documents is how the
current state happened.

## Route to 57 codes (execute in this order — steps collide if overlapped)

1. **Fetch lands** (workflow wcvichmb0). Its verify phase hashes every download
   against the PDFs already in ~/Downloads/forms. EXPECT REJECTIONS: early
   evidence shows agents fetching the CURRENT revision (1600-PT, 1603Q,
   2000-DST) where the legacy form (1600 Sep-2005, 1603 Nov-2004, 2000
   Jan-2018 DST) was asked for. A download that duplicates an existing sha256
   is NOT a new form. Record each such code as unavailable rather than
   converting the same PDF twice under two names -- that would inflate the
   count while covering nothing, which is the exact failure this whole
   correction exists to prevent.
2. **Gate finishes** before any batch run. batch.py and gate.py cannot run
   together: the gate regenerates, and a concurrent batch invalidates its
   corpus mid-measurement.
3. **Convert the genuinely-new forms.** Run batch.py over the expanded source
   set. Each new form goes through the same pipeline with no special-casing;
   any that fails extraction is reported, never hand-patched.
4. **Re-gate** the expanded corpus. New forms will surface new assertion
   failures -- that is the pipeline working, not a regression. Triage them the
   same way as the existing four checks.
5. **Round-4 visual review** over ALL codes, new ones included.
6. Only then is the corpus complete. The PR still does not merge until the
   user has reviewed.

If a BIR form is genuinely not published anywhere on bir.gov.ph, record it
under `## Blocked` with what was tried and move on. Never substitute a mirror,
a third-party copy, or a different revision presented as the missing one.

## User decisions (2026-08-06) -- binding

1. **A producer may not certify its own promotion.**
   `source_certified_replacement_owner` (lattice.py:3979-4006) promotes a
   gate-blocking `retained_unresolved` comb to `active_resolved` on the
   producer's own evidence, and substitutes the subject's identity (2550M's
   `p1c99` disappears from the ledger, replaced by `p1c193`). The restored comb
   is REAL -- a printed comb that had no input now has one -- so the outcome
   stands; the mechanism does not. Promotion must require the COMB REFEREE's
   independent agreement: it derives comb geometry from raw PDF ops and already
   proved this subject's 4 slots at exact 11.04pt pitch. The referee is the
   adjudicator everywhere else; this is the one promotion path that bypassed it.

2. **An exclusion must publish what it excluded.** The gate's assertion-detail
   schema accepts only counts, which blocked two correct fixes
   (`image_transform_applied`'s guide-relocated placements,
   `money_boxes_have_inputs`' pre-printed boxes). Extend the schema to carry
   lists, so an exclusion is inspectable rather than trusted. Shipping the
   exclusion while reporting only a number would be a hidden weakening -- the
   agent that stopped rather than do that was right.

## Method

- Work in increments; after each, run the affected self-tests, and run the
  full gate before claiming a check moved.
- **One agent per file.** Two agents on emit.py once cost a day.
- A change to extract.py names its caller in the same increment.
- A schema change (batch record keys, provenance keys, manifest shape) is
  declared everywhere it is asserted — gate.py BATCH_RECORD_KEYS and the
  gate's own self-test fixtures — in the same commit.
- comb_referee.py pins its producers by sha256; editing audit.py, extract.py
  or lattice.py requires re-pinning as part of the same commit ritual.
- Never weaken verify.py tolerances; never special-case on form code; the
  pipeline never rasterises (humans reviewing may).
- Findings resolve in the ledger with evidence, in the same commit as the fix.

## Constraints that cannot be broken

Unchanged from the previous goal: exact tolerances (position 0.25pt,
thickness 0.05pt, advance 0.10pt, size 0.01pt); decorative greys stay grey;
deterministic byte-identical output; forms/ is hand-maintained; main stays
clean; report a cost rather than trade it. The official BIR PDFs are never
committed. Judgement calls already made (SVG rule layer, crispEdges off,
MediaBox paper, Arial Narrow via scaleX, clipped straddlers, moved 2551Q
pins) are recorded in git history at b2bd2e9 and stand unless measurement
overturns them.

## Progress

- 210044a ci: PyPI playwright pin (npm 1.58.2 does not exist on PyPI; Python
  line is 1.59.0). First honest CI execution pending.
- Everything before: see git log. Integrity increment complete — gate back to
  its 4 pre-existing failures, three prove-phase faults (f1/g/c2) now caught,
  all 10 self-tests pass, tree byte-identical.

## Blocked

**The comb referee's 53 reviewed HTML pins are stale, and re-pinning them is a
user-review action, not an agent action.**

`comb_referee.EXPECTED_HTML_STRUCTURE_SHA256` hashes every emitted byte per
form; its docstring says "any change requires an explicit pin review". All 53
now mismatch, because today's three producer fixes (extract digest, lattice
slivers + comb ownership, audit assertions) plus the two new forms changed
every bundle's HTML. The referee therefore reports 53 errors and the gate's
comb-referee check is UNEVALUABLE -- which is a FAILURE, not a pass.

The pin did its job. The question it asks -- "were these changes reviewed?" --
is exactly the question the user's decision 1 says a producer may not answer
about itself. I re-pinned the two NEW forms (they had no prior reviewed value
to overwrite: 1604cf-2008 621ddec5, 2200an-2018 c794b756) and the corpus
census (EXPECTED_FORMS 51->53, EXPECTED_COMBS 4442->4539, plus per-slug counts
1604cf-2008: 10, 2200an-2018: 87). I did NOT touch the 51 existing hashes.

Evidence that the changes are sound, for whoever reviews: gate r4 scored
rules, paper, artwork, text and conversion PASS on 53/53, determinism
byte-identical, audit-refresh PASS.

DESIGN TENSION worth deciding at the same time: a whole-file hash over 53
generated documents means EVERY legitimate producer change invalidates all 53
and needs a fresh review round. That is either deliberate maximum conservatism
or unworkable friction -- if the latter, the pin should hash the STRUCTURE it
claims to (the tag/attribute skeleton it already enumerates) rather than every
byte, so that a geometry fix does not read as a structural change.

Note for whoever reads this next: the 9 forms below were once recorded here as
"superseded by the quarterly versions". That was wrong, and checking the
official list is what corrected it -- BIR's coverage table numbers 1600, 1601-E,
1601-F, 1602, 1603 and 1604-CF as their own entries ALONGSIDE 1600PT/VT,
1601EQ, 1601-FQ, 1602Q, 1603Q and 1604-C/F. A legacy monthly return is a
separate form, not an old name for the quarterly one. 1704, 2000 and 2200AN
were absent outright. Fetch workflow: wcvichmb0.
