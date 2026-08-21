# STATUS — shipped at gate 12/13 by user direction, 2026-08-14

The feature (the 53-form fillable-HTML corpus with the user's whole visual
review closed) ships at **gate r71: 12 of 13 checks PASS**, the 13th
(`comb-referee`) failing only on its itemized, evidenced remainder — the 30
retained ledger subjects plus one double-count — per the user-directed
amendment in GOAL.md. The check stays computed and reported on every run;
closing it is the next objective (`COMB-REFEREE-CAMPAIGN.md`), which is
structurally gated on 115 user reviews and two measurement programs.

Feature facts at ship (all measured, none inherited): 53/53 forms, corpus
tab-walk green, 45,549 inputs, `inputs_over_printed_text` 0/0, all 10 audit
assertions hold on 53 forms, findings ledger 0 open blocker+major (232 filed,
150 blocker+major all resolved), determinism byte-identical `b9d71850a8c6`
across r65–r70, CI 11/11 green.

# STATUS — formgen, measured state

**Update rule: any commit that moves a number below updates this file in the
same commit.** This is the only formgen document allowed to hold measured
status numbers (`GOAL.md` owns that rule; `README.md` owns the process).

Measured 2026-08-11 over the 53-form corpus, on branch `gol/form-correction`,
regenerated at the r43 producer bytes. Assertion counts are from a corpus-wide
`audit.py --assertions-only` run over that regeneration, measured on the tree
that run scored; the r27 section below is kept as written and its numbers are
superseded by the r43 section.



## Z1 — ten reviewed topology facts land; three are withdrawn on measurement

**`comb_slots_match_printed` 9 forms / 13 offenders -> 3 forms / 3**, verified on
a clean full 53-form `audit.py --assertions-only` (an earlier run reported
"9 forms, no failing assertions" and was discarded as truncated -- it had been
interrupted by a concurrent self-test). `decided_by_review` = 10, exactly the
ten pinned facts. The three that remain are exactly the withdrawn trio.

The registry (W8) is stricter than the package asked for, in the ways that
matter: it supplies a compartment COUNT only and never divider positions --
position evidence is published as unavailable **with its reason** rather than
fabricated from a count; it is consulted only where `printed_compartments`
already raised; an entry for an independently-decidable subject is an ERROR;
a `source_sha256` mismatch is an ERROR; and **a reviewed count that disagrees
with what we emit is still an offender**, so it can resolve "I cannot tell"
but can never force agreement. Decided subjects publish
`layout_relation: decided-by-review` and are listed separately, so human-
reviewed and machine-measured facts stay distinguishable in `build/audit.json`
permanently.

**Three of the user's thirteen confirmations were WITHDRAWN rather than
pinned, and the reason is a lesson about what a review answers.** Measuring
2200A `p1c111`, 2200C `p1c107` and 2200P `p1c110` before committing showed
their slot 0 is **173.66pt wide against the comb's own 14.52pt pitch** and
holds the row's printed caption ("27 Tax Debit Memo") -- not a character
compartment. And the source prints ticks at x 117.4, 131.9, 146.4, 160.8 and
175.3, **inside that caption region at the same pitch**, so the sheet's tick
row runs on underneath the caption. "How many writing compartments" is
therefore genuinely open there, which is what the audit's competing readings
[5, 8, 29] were saying all along. The review sheet established that OUR RENDER
MATCHES THE OFFICIAL SHEET -- true, and a different question from the one the
registry asks. Filed as **F229** instead, recording both halves as one
question: slot 0 has no `<input>` AND is not a compartment, and those three
carry `invalid-emission` independently of topology (audit.py:8041-8042), so no
topology fact could have cleared them regardless.

## W8 — the reviewed-topology registry, shipped EMPTY

**Measured 2026-08-13, worktree `wt/w8-registry`, base `3b633cce`.** No
producer file touched (`extract.py`, `lattice.py`, `emit.py`, `fonts.py`,
`guides.py`, `correct.py`, `batch.py` all byte-identical to base, confirmed by
`git diff`); only `audit.py` (the locked judge, extended per its own
review-bundle rule) and `comb_referee.py` (one pin comment + hash) change.

`check_comb_slots_match_printed`'s 13 remaining `source-topology-unevaluable`
offenders (9 forms) are exactly the population the source's own vector
operators cannot settle -- competing band/tone topologies, ambiguous U-frame
ownership, no strict-majority reading -- which W5 measured as far as the
audit's own evaluation can honestly go. The designed route past a limit of
*measurement* is human review, the same way
`scripts/audit_html_form_migration.py` keeps its trusted-producer registries
as empty frozensets until the user reviews a producer. `REVIEWED_COMB_TOPOLOGY`
(new, `audit.py`) is that registry for comb topology: keyed by
`(slug, page, cell_id)`, consulted **only** for a subject whose own verdict is
already `source-topology-unevaluable`, and **shipped EMPTY**. The 13 reviewed
facts land in their own commit once the user confirms them; this package adds
only the mechanism.

**Every guard is load-bearing and self-test-proven, not merely asserted:**

- A registry entry for a subject the audit can already decide from vector data
  is an ERROR (`reviewed-comb-topology-invalid`), never silently ignored --
  the guard that stops the registry ever overruling a real disagreement.
- An entry whose `source_sha256` does not match the current IR's own
  `source.sha256` is an ERROR, not a stale-but-usable fact.
- An entry missing any of its eight required fields (`compartments`,
  `source_sha256`, `page`, `cell_id`, `bbox`, `reviewer`, `date`, `citation`)
  is an ERROR, not a skipped entry.
- Two further guards beyond the four the task named, in the same fail-closed
  spirit: the entry's own `page`/`cell_id` must match its registry key
  (transcription check), and its `bbox` must match the active layout cell's
  own rectangle within 1e-6pt (the same staleness class as the sha256 check --
  a cell that moved since review is not the cell that was reviewed).
- No entry -> the subject stays `source-topology-unevaluable` exactly as
  today.

A subject the registry decides publishes `layout_relation
== "decided-by-review"` (never silently indistinguishable from one the audit
measured itself) and is separately listed under the assertion's new
`decided_by_review_subjects`, alongside a `decided_by_review` count. It
supplies a compartment COUNT only -- divider positions stay reported
unevaluable, with an explicit reason, exactly as they do today; nothing here
lets a reviewed fact stand in for a measured rail position.

**With the registry empty, every number is unmoved**, verified by a full
53-form `audit.py --assertions-only` run (`build/audit-w8.json`, uninterrupted
by any edit mid-run): `comb_slots_match_printed` **9 forms/13 offenders**
(1604cf-2008:1, 1604f-2018:1, 1801-2018:2, 2000-dst-2018:1, 2200a-2020:3,
2200c-2018:1, 2200p-2020:1, 2551m-2002:2, 2553-1999:1 -- exactly the 13-cell
sheet sent to the user), `decided_by_review` **0 on every one of the 53
records** (proof the empty registry decided nothing), `inputs_over_printed_text`
**0 forms/0**, every other assertion unmoved, all 53 forms `status: "ok"`.
`comb_referee.EXPECTED_HTML_STRUCTURE_SHA256`'s all-53 pins were independently
hashed against the actual `build/html/*.html` bytes on this tree: zero drift,
which is the same invariant the comb censuses and input count are computed
from, so **comb censuses 4,587/4,557/30** and **input count 45,548** are
unmoved by construction (not independently re-derived by a fresh full-roundtrip
`comb_referee.py` run, which this package's own byte-identity proof makes
unnecessary). Tab-walk **53/53 green** (2316-2021 green=177, matching its own
prior recorded figure exactly), blue (vacant) census **5, unmoved**
(0605-1999:1, 1604cf-2008:1, 2550m-2007:3).

**Self-tests, all pass:** `extract.py --self-test` (7 pinned PDFs, 24 checks,
24+24 probes), `lattice.py --ir build/ir/2551q-2018.ir.json` (489/264 slots,
unchanged), `emit.py --self-test`, `comb_referee.py --self-test`, `gate.py
--self-test`, `tab_check.py --self-test`, `validate_tree.py --self-test`,
`fixtures/prove_fixtures_fail.py` (19 mutations, 6 contract-only), `audit.py
--self-test` (0 failures, including 5 new W8 checks: a valid entry decides an
otherwise-unevaluable subject with the right count; an entry for an
independently-decidable subject fails closed; a mismatched-sha256 entry fails
closed; an entry missing a required field fails closed; no entry leaves a
genuinely unevaluable subject exactly where it was -- each proven by injecting
a synthetic `REVIEWED_COMB_TOPOLOGY` entry and removing it again in a
`finally`, and cross-checked by a forced-failure monkeypatch that made all
five fail together, then restoring cleanly).

**Determinism:** two independent `batch.py` runs into scratch `--out`
directories (never `forms/`) produced byte-identical tree digests
(`288e84d4a18d8faa37ff662dcc4e65430cd219e669144ad81630fda493c5a28d`).

**Pin:** `comb_referee.AUDIT_PRODUCER_SHA256` re-pinned
(`346e293028c58890b410a69d2f49f14526306f04e39f847d4ad015112499e6c0`), cause
recorded inline at the pin. `EXPECTED_HTML_STRUCTURE_SHA256` **unmoved** --
verified directly against `build/html`'s current bytes, above, rather than
inferred from `comb_referee.py` never raising.

**Operator guide:** `tools/formgen/review/README.md` documents the entry
schema and the exact steps to add a reviewed fact, so populating the registry
later is mechanical.


## Z3 — F065 closes: the cause was a subset-tag key mismatch and the universal fontbuffer font-box barrier, not an unresolvable font (1707-2021 item 9 gains an input)

**Measured 2026-08-13, worktree `wt/z3-f065`, base `3b633cce`.** F065's own
recorded cause -- "1707-2021's item 9 blank is UNEMBEDDED Arial Narrow, which
MuPDF's name cleaner cannot resolve" -- was wrong, though its refusal was
truthful. Measured directly on the pinned PDF: 1707-2021 page 1 carries
`ABCDEE+Arial Narrow` at xref 28 with `ext='ttf'` -- a real, EMBEDDED
TrueType program, subset-tagged per the PDF spec (ISO 32000-1 9.6.4: six
uppercase letters then `+`), plus `,Bold`/`,Italic` siblings, also embedded.
`substitutable_faces` registered it under its exact `/BaseFont`, but MuPDF's
own rawdict strips the subset tag from `span["font"]` before this module
ever sees it, so every span asking for the face by its stripped name found
nothing -- a key mismatch, never a name MuPDF's cleaner was even asked
about (embedded fonts never reach that branch). Corpus-wide, the SAME
mismatch was booking 61,781 of the corpus's 62,010 "no face is resolvable
for this font" glyphs; only 229 (unembedded Tahoma, 1604cf-2008/2553-1999)
are genuinely unresolvable, confirming F065's ORIGINAL account was Tahoma's
shape, misattributed to 1707.

**Two fixes, in one package.** (a) `substitutable_faces` now registers an
embedded face under its tag-stripped name too (`SUBSET_TAG_RE`, exactly the
spec's six-uppercase-letters-then-`+` pattern, never a looser one), additive
only -- an exact-key hit is never displaced by a stripped one. Measured to be
inert on its own: every embedded, buffer-loaded face MuPDF loads still
answers `glyph_bbox` with its own whole `Font.bbox` for every codepoint,
never a real per-glyph outline (`glyph_ink_box`'s own documented barrier,
now corpus-confirmed rather than assumed) -- so fixing the key alone moves
61,781 glyphs from "no face is resolvable" to "no single face states this
glyph's outline" (9,217/48 forms -> 70,963/49 forms) and publishes nothing
new; `glyph_ink_measured` stays exactly 279,101, corpus-wide, both before and
after. (b) New function `embedded_glyph_outline` hand-parses one glyph's own
outline and advance from an embedded TrueType program's own
`head`/`loca`/`glyf`/`hmtx` bytes (mirroring `fonts.py`'s own WOFF2
table-directory reading -- no new dependency), fed into `ruled_blank_bars`
ONLY -- never the corpus-wide `GlyphOutlines` measurement -- with the parsed
advance cross-checked against the run's own stated `char_widths_pt` before
being trusted. Together: 1707-2021's item 9 blank -- the corpus's one ruled-
blank refusal -- now publishes at (471.78, 359.61)-(583.52, 359.96)pt, 0.35pt
thick; `ruled_blank_groups`/`published`/`refused` moves 119/118/1 -> 119/119/0,
corpus-wide. The 35 underscore glyphs that used to sit in the caption's text
run leave the corpus's `glyph_ink` census entirely (published as a rule
instead), so the corpus's own `glyph_ink_glyphs` denominator moves
356,092 -> 356,057; nothing else about the census's shape (78.4% measured,
one character not measurable everywhere it's set 558, advance contradicted
5,173, glyph id/codepoint not stated 20/13) moves at all.

**`RuledBlankWriting`, the corpus's existing 118-strong mechanism, seats the
new field with no special case.** Cell `p1c214` (`data-cell-kind="label"`)
now carries an `<input>` at `inset:0pt 10.96pt 0.17pt 67.14pt`, bottom-seated
on the new rule with ~9.7pt headroom, exactly as every other ruled-blank
field is. Item 9's second, previously-independent barrier (the knockout
band's caption run covering the whole knockout box, per W1/F206's own prior
measurement) is now moot: the caption's own underscore run is no longer
"unresolved," so it leaves the run entirely instead of blocking the whole
knockout by its own bounding box.

**Browser-verified, the user's own way**: `page.goto(file://...)`, Tab from
the page's first input, typed, read back. Cell `p1c214-i` reached at **Tab
press 64**, typed `SPECIFIED ANSWER`, read back verbatim,
`text-align: center` inherited from `RuledBlankWriting` like every other
ruled-blank field.

**Blast radius, measured on the tree this package actually wrote:** ONLY
`forms/extra/1707-2021/{index.html,form.css,provenance.json}` changed --
confirmed both by `git diff` against this task's own base commit and by two
independent `batch.py` regenerations (`forms`, `build/ir`, `build/layout`
tree digests byte-identical across both runs; `build/html` byte-identical
once the shared, out-of-scope `fonts/` staging directory is excluded from
the comparison). Input count moves by exactly +1, corpus-wide: 1707-2021's
own raw `<input>` count moves 999 -> 1000; `tab_check.py`'s browser-measured
(live-DOM) total across all 53 forms on this tree is **44,664**, with
1707-2021 itself green=997. `inputs_over_printed_text` stays 0 forms/0
offenders. `comb_slots_match_printed` unchanged at 9 forms/13; comb censuses
unchanged (`comb_referee.py`'s own full run: `combs_expected`/`combs_found`
4587, `subjects_active` 4557, `subjects_retained_unresolved` 30, `forms_error`
0). Blue (vacant) census 5, unchanged. Corpus tab-walk **53/53 green**
(`tab_check.py`, 219.4s, red-skipped 0 / red-order 0 on every form). A full
`audit.py --assertions-only` run over the regenerated corpus confirms 53
forms scored.

**Two new source-level mutations**, both against a new, wholly independent
written-here probe page (`ruled_blank_embedded_probe_ir`, its own check
`ruled-blank-embedded-subset`, deliberately never sharing pinned state with
the existing `ruled_blank_probe_ir`/`check_ruled_blank_split` so a mutation
here can never collide with theirs): `mutate_ruled_blank_embedded_subset_tag`
lowercases the probe's own good font's spec-shaped subset tag -- MuPDF's own
rawdict stripping is looser than the PDF spec (measured directly: it also
strips a lowercase or digit-bearing six-character prefix) but
`extract.SUBSET_TAG_RE` correctly does not, reproducing F065's exact key
mismatch for a tag that is not spec-shaped and tripping the check;
`mutate_ruled_blank_embedded_program` un-corrupts the probe's own
deliberately-truncated second font (its `glyf` table is shortened so
`loca`'s own offsets for the underscore glyph run past its end, every other
table byte-identical to the working one) by replacing it with the working
program, proving the refusal was genuinely about THIS program's own bytes.
`prove-fixtures-fail: PASS over 21 source-level mutations` (up from 19), 6
checks stated as contract-only, unmoved.

**Self-tests, all pass:** `extract.py --self-test` (7 pinned PDFs, now 25
checks and 25+24 probes, up from 24 and 24+24) and `--self-test --fixtures`
(6 fixture PDFs, unchanged -- no fixture PDF needed extending, since the new
shape reaches past `make_fixtures.py` the same way the existing ruled-blank
and glyph-ink probes already do); `lattice.py --ir
build/ir/2551q-2018.ir.json` (489/264 slots, unchanged); `emit.py
--self-test` (every corpus assertion 0 offenders, including the ruled-blank
corpus check: 53 forms, 59 rule(s) claimed, 0 without a typing surface);
`comb_referee.py --self-test` and a full run (`forms_error: 0`, censuses
unmoved); `gate.py --self-test`; `tab_check.py` (53/53 green); `validate_tree.py`
(7/7); `fixtures/prove_fixtures_fail.py` (21 mutations, above).

**A necessary side fix, unrelated to F065's own subject but exposed by
adding a 25th check:** `self_test`'s own "did every paint-span contract
probe run" guard compared `mutations_ran`/`contracts_ran` against
`len(SELF_TEST_CHECKS)`, which happened to equal `len(PAINT_SPAN_CONTRACT_
CASES)` (24) by coincidence, not by contract -- the two counts are
unrelated. Pulled the 24 contract-probe cases out to their own named module
constant so this guard is keyed to the right count and stays correct as
`SELF_TEST_CHECKS` grows; no case, tolerance or check moved.

**Pins.** `comb_referee.AUDIT_DEPENDENCY_SHA256["tools/formgen/extract.py"]`
re-pinned (extract.py's own bytes moved). `comb_referee.
EXPECTED_HTML_STRUCTURE_SHA256` re-pinned for the one slug whose
`build/html/<slug>.html` bytes moved (`1707-2021`; the other 52 are
byte-identical), appended as a new re-pin round per this file's own
changelog-dict convention. No comb census, extract.py check count beyond the
one new check, or tolerance moved. `review-findings.json`: F065 closed
`fixed` with the browser proof above; new finding F229 (minor, open) records
the general case this package deliberately does not fix -- the fontbuffer
font-box barrier still blocks real per-glyph outline measurement for 70,963
glyphs on 49 forms corpus-wide, and widening `embedded_glyph_outline`'s
scope beyond the one `RULED_BLANK_CODEPOINT` the ruled-blank path needs is
real, unmeasured reach.

**Determinism:** two full `batch.py` regenerations over the 53-form corpus
produce byte-identical `forms`, `build/ir` and `build/layout` trees
(tree digests match exactly: `forms` `b104d8a7b95e`, `build/ir`
`7d3803fc87b0`, `build/layout` `a13596c21acd`) and byte-identical `build/html`
once the shared `fonts/` staging directory (populated by a fixed,
`--work`-independent default path, not per-run output) is excluded from the
comparison (`build/html` `49fc02e20a00` both runs).

## r59 — everything agent-closable is closed; the last input is the user's

**Gate r59: 10/13**, determinism byte-identical (`15947889ee6a`). The three
remaining reds now reduce to ONE pending input plus one approved exception:

- `assertions` — only `comb_slots_match_printed`, 9 forms / 13 offenders,
  every one a cell the audit provably cannot decide from vector data and every
  one on the 13-cell review sheet sent to the user (official crop vs ours, red
  box on each cell). Their counts become pinned reviewed facts (W8).
- `findings` — **2/149, exactly the user-approved exception list**: F065
  (writing area under a correctly-refused font's text) and F222 (honest fix
  measured at 2,428 cells / 17 forms and refused per the project's own
  calibration). Impossibility proofs attached to both.
- `comb-referee` — the same 13 offenders seen through the audit-complete
  guard; clears with W8.

W9 closed F226's two bindable sites (+2 inputs, browser-verified, centred,
bottom-seated 0.42pt above their rules; the wrong-binding case refused at
22.8pt by both tests independently; corpus-wide the rule binds exactly the 2
target cells with 250 candidates refused, both directions asserted). The
orphan third site is F228 (minor): its caption run is assigned to no lattice
cell, unreachable by any cell-to-cell mechanism.

Final corpus numbers at r59: **45,548 inputs** (45,333 at the session's
start), tab-walk **53/53 green**, blue census **5**,
`inputs_over_printed_text` **0/0**, censuses 4,587 / 4,557 / 30.

## W9 — 2316's own item 53/54 sign, item 56 stays open and is spawned as F228 (F226 closed)

**Measured 2026-08-13, worktree `wt/w9-f226`, base `b9af586b`.** `emit.SignatureRuleWriting`'s
caption search (F221's own mechanism) now bridges a genuine vertical GAP
between a rule-owning `label` cell and its caption, not only the exact
shared wall F221's own 9 sites and 2316's own item 55 already prove. The
gap is bridged only when it is smaller than the form's own `glyph_height_pt`
(`lattice.min_fillable_line_metrics`'s own sliver-rule metric, no new
constant) AND carries no printed ink anywhere across the claimed rule's own
x-extent, checked with a new helper (`_gap_has_ink`) that re-verifies
`PrePrintedInk.intrusions`'s own coarse pre-filter against each glyph's own
precise box on both axes. Deliberately geometric (candidate caption cell's
own top minus the rule-owner's own bottom), never a cell hop, so 2316-2021's
own 0.54pt ungridded hole (no lattice cell at all) needs no special case.

2316-2021's item 53 (`p1c324`, owns rule h180) bridges a 1.32pt ink-free
gap, across a genuinely blank sliver cell (`p1c326`, kind=blank, no
caption of its own), to reach `p1c327`'s "Present Employer/Authorized
Agent Signature over Printed Name." Item 54 (`p1c328`, owns rule h183)
bridges a 0.54pt ink-free gap -- a span the lattice made no cell for at
all -- to `p1c330`'s "Employee Signature over Printed Name." Both
browser-verified: focus, type, read back, `text-align: center`, seated
0.42pt above each one's own rule (half its 0.84pt thickness), tab order
continuing into the adjacent date comb.

**The wrong-binding case this task named stays refused, measured, not
assumed.** `p1c322`'s own rule h178 (the jurat-paragraph/field-divider
rule) reaches for `p1c327`'s caption 22.8pt away -- refused on BOTH
grounds independently: 22.8pt is more than 4x this form's own 4.6476pt
`glyph_height_pt`, and the gap itself carries real printed ink ("Date
Signed"/"53") across the rule's own x-extent. A row that holds text is at
least one glyph tall by definition and can never fit under
`glyph_height_pt`.

**Corpus-wide, not just 2316-2021.** Every label cell's owned rule across
all 53 forms was independently re-derived old (`metrics=None`) vs new
(with `metrics`); the extension binds exactly the two 2316 sites above and
NOTHING else. A parallel near-miss census (every candidate caption within
a genuine gap under 200pt, whether or not it ultimately binds) found 24
non-2316 near-misses, 60.0-198.51pt each; every one exceeds its own form's
`glyph_height_pt` AND carries real ink, so the guard is never exercised at
a hairline anywhere in this corpus. New standing corpus assertions sum
`SignatureRuleWriting.gap_bound_count()`/`.gap_refused_count()` over all
53 forms and assert `count > 0` both directions: 2 rules gap-bound
corpus-wide, 250 real candidates refused corpus-wide.

**Item 56 (`p1c339`, owns rule h194) stays open, spawned as F228.** Its
own caption run `p1t218` is confirmed absent from every cell's own
`text_run_ids` on the page -- the identical orphan-run shape
`_signature_line_caption`'s own docstring already names. The F226
extension is geometric over CELLS; an orphan run with no owning cell has
no "candidate cell's own top" for this test, or any cell-to-cell
adjacency test, to read at all. Closing it needs a `lattice.py` change to
run-to-cell assignment, out of this class's own scope and this worktree's
own boundary (`lattice.py` untouched).

**A real source-level mutation**, `make_fixtures.signature_rule_gap_row`
(fixtures/rules.pdf) -- a rule-owning label cell, a genuinely blank sliver
cell, a caption cell, reproducing 2316's own item 53 at fixture scale --
with `prove_fixtures_fail.mutate_signature_rule_gap` widening the sliver
past the fixture's own `glyph_height_pt` (3.0pt -> 15.0pt), the same
wall-move idiom `COMB_BAND_REUNIFICATION_NOTCH_SIZE_PT` already set.
`prove-fixtures-fail: PASS over 19 source-level mutations` (unmoved) plus
this new one (`prove_signature_rule_gap`), run separately.

Measured on the tree this package wrote: input count **45,546 -> 45,548
(+2** -- not +3; item 56 does not gain an input because it cannot).
`inputs_over_printed_text` **0 forms/0, unmoved.** `comb_slots_match_
printed` **9 forms/13, unmoved** (this package never moves a slot count
or divider). Comb censuses **4,587 subjects / 4,557 active / 30 retained,
unmoved.** Blue (vacant) census **5, unmoved.** Tab-walk **53/53 green**
(2316-2021 green=177, exactly 175+2). Self-tests pass: `extract.py
--self-test` (7 pinned PDFs) and `--self-test --fixtures` (6 fixture
PDFs, re-pinned), `lattice.py --ir build/ir/2551q-2018.ir.json`,
`emit.py --self-test`, `comb_referee.py --self-test` and a full run
(`EXPECTED_HTML_STRUCTURE_SHA256` matches on all 53 after the
2316-2021 re-pin), `gate.py --self-test`, `tab_check.py` (53/53 green),
`validate_tree.py` (6/7 -- the one failure, a staged-but-unreferenced
asset, is pre-existing and unrelated, matching `batch.py`'s own
documented "one unreferenced asset" precedent), `fixtures/
prove_fixtures_fail.py` (20 source-level mutations total).

Pins moved: `comb_referee.EXPECTED_HTML_STRUCTURE_SHA256['2316-2021']`
(the only slug whose `build/html` bytes moved, appended as a new re-pin
round per this file's own changelog-dict convention). `extract.
FIXTURE_FIXTURES['FIXTURE-RULES']` (fixtures/rules.pdf gained
`signature_rule_gap_row`'s new shape). `comb_referee.
AUDIT_DEPENDENCY_SHA256['tools/formgen/extract.py']` (extract.py's own
bytes moved with its pin-table comment). No comb census, extract.py
check count, or tolerance moved.

**Determinism:** two full `batch.py` regenerations over the corpus
produce byte-identical `build/ir`, `build/layout`, `build/html` and
`forms/` trees (`diff -rq`, 0 differences).



## r57–r58 — the assertion that started it all reaches zero, and the failure surface is now fully mapped

**Gate r58: 10/13**, determinism byte-identical (`c05717841c8e`).
**`inputs_over_printed_text` no longer appears on the gate** — 0 forms / 0
offenders, the first corpus-wide zero in this project's history. W6/F227's
diagnosis: the trim existed and was correct, but never REACHED three combs
(field_box's comb branch never called it) and trimmed two plain fields to the
run's declared line box instead of the glyphs' measured outlines (0.055pt
short — 'p' and 'y' descend 0.218em, the line box says 0.21). The seventh
logged defect of the shape "a nominal edge standing in for measured ink."

**W5 merged with the user's approval** — the one edit the locked judge
permits, reviewed before merging. `comb_slots_match_printed` 10 forms/19 →
**9/13**; the referee evaluates end-to-end for the first time.

**One integration defect, mine to own:** F227's fix expressed the comb trim
as `style="inset:<T>pt 0pt 0pt 0pt"` on slot inputs, whose referee grammar
pinned an exact key set with no `style` — gate r57 correctly rejected all
three changed forms ("outside the emitter grammar"). The fix was right; the
schema change was never DECLARED, and declaring pins is the single-writer's
job, not the package's. Declared at r58: the grammar admits `style` on slot
inputs constrained to EXACTLY a top-only positive inset, factored into
`slot_input_style_ok()` and proven able to fail with three reject probes
(non-top-only inset, non-inset property, second declaration), so the channel
cannot silently widen.

**The remaining failure surface, fully mapped:** `assertions` = 13 comb
offenders across 9 forms, every one waiting on the user's 13-cell topology
review (the sheet is with them; their counts become pinned reviewed facts —
W8); `findings` = F065 + F222 (the two user-approved done-condition
exceptions, impossibility proofs attached) + F226 (2316's three signature
sites, W9 running on the sliver-gap rule); `comb-referee` = the same 13
offenders seen through the audit-complete guard. Nothing else is red.

## W6 — the five assertion offenders close honestly, and a comb gets ink-trimmed for the first time (F227 closed)

**Measured 2026-08-13, worktree `wt/w6-inktrim`, base `24cdc6e7`.**
`inputs_over_printed_text` **0 forms / 0 offenders** (was 2 forms/5:
1604cf-2008, 2316-2021), independently re-verified by loading all 53
bundles and calling `audit.check_inputs_over_printed_text` directly (0
errors, 0 offenders), then confirmed on a full `audit.py --assertions-only`
run over the regenerated corpus. `comb_slots_match_printed` **10 forms / 19
offenders, unmoved** (this package moves no slot count and no divider; the
concurrent W5 lattice.py package is not merged here). Every other
assertion **0 offenders, all 53 forms, unmoved**. Comb censuses **4,587
subjects / 4,557 active / 30 retained, unmoved**, measured directly over
`build/layout`. Input count **45,546, unmoved**. Tab-walk **53/53 green**;
blue (vacant) census **5, unmoved**.

**Two mechanisms, not one, and the finding's own request to diagnose before
fixing paid off: fixing only the first would have left three of the five
offenders open, and fixing only the second would have created a NEW wrong
1.15pt over-trim on 1604cf-2008's Zip-code comb.**

**(1) Not offered at all.** 1604cf-2008 p1c16 and 2316-2021 p1c38/p1c39 are
combs. `field_box`'s comb branch never called
`writing_box_clear_of_printed_ink` at all — `comb_writing_rect`'s own
rectangle is deliberately exempt from ink (it is
`comb_referee.writing_band_corroboration`'s contract with the source's
painted walls), and that exemption stays correct, but nothing was reading
the INPUT nested one level inside it. `comb_writing_top_clear_of_printed_ink`
(new) runs the identical trim over a comb's own writing rectangle and
applies the result as an `inset` on every slot's `<input>` — never on the
slot `<div>`, which stays byte-identical on every affected form (verified:
`comb_slots_markup`'s left/top/width/height and `cell_json`'s `comb.y`/`h`
are unchanged everywhere). Only the TOP component is read: the same trim
also fires LEFT on 7 unrelated compartments corpus-wide (0605-1999 p1c3,
2551m-2002 p1c74/p1c79/p1c86, 2553-1999 p1c79/p1c84/p1c91), and a comb's
height is already shared across every slot (one face size, fit once) while
its width is not (one compartment each) — a shared left/right inset would
shrink typing area in slots the ink never reaches, so it is deliberately
left to whatever narrower, per-slot fix that population eventually needs.
The same shape as F207: a box never offered to the trim is now offered, on
the one axis the row genuinely shares.

**(2) Seen but trimmed to the wrong bound.** 2316-2021 p1c62/p1c83 were
already offered and trimmed, but `intrusions` restated the run's shared
line box (ascent line to the face's DECLARED descent line) for every glyph
in it. 'p'/'y' measure -0.218em of real outline against a declared -0.21em,
so the line box was 0.055pt short of the ink it stood in for. `intrusions`
now reads each glyph's OWN outline (`_glyph_ink_box`, ported from
`audit.published_glyph_ink`'s own math the way `RULE_ORIGIN_TEXT_UNDERSCORE`
is already ported from `extract.py`) where the source states one, falling
back to the run's shared line box only where it does not.

This is reach in BOTH directions on the same corpus, never a threshold:
2316-2021's 'p'/'y' now measure deeper than the line box promised (closing
p1c62/p1c83 to exact contact — the box grows by exactly 0.0548pt on each),
while 1604CF's "Zip Code" 'e' — no descender, but sharing a run with 'p' —
now measures its own real ~0.02em instead of the whole run's deepest
character. That second direction is why offering combs the trim (fix 1)
does NOT create a false 1.15pt over-trim on 1604cf-2008 p1c21 (Zip-code
comb): with the coarse, run-line-box measurement alone, fix 1 by itself
would have wrongly trimmed a cell with no real ink threat. Both fixes were
required together.

**A third offender the pre-fix sweep did not name closes as the same
reach, not invented:** 1701ms-2024 p1c184 ("Others" caption, an unmeasured
face that correctly falls back to its line box) also gets a comb top-clear
(0.18pt), found by the corpus-wide sweep the fix's own verification
requires. Every comb top-clear in the corpus: before either fix, 11
candidates (coarse line-box measurement only); after both, exactly **4** —
1604cf-2008 p1c16 (0.7142pt), 2316-2021 p1c38/p1c39 (0.2198pt each),
1701ms-2024 p1c184 (0.18pt) — all closing to exact contact. The other 7
(1604cf-2008 p1c21 1.15pt, 1600wp-2010 p1c24 0.63pt, 2316-2021 p1c37/p1c40
0.38pt each, 2550m-2007 p1c89/p1c90/p1c91 0.14pt each) resolve to 0 once
the glyph is read precisely; none of the 7 was ever an audit offender, so
nothing regressed by them going untrimmed. The same re-measurement also
takes the 7 LEFT-nonzero compartments named above to exactly 0 on every
axis, so the vertical-only restriction on combs costs nothing observable
in this corpus — but it stays, because it is not evidence-dependent.

**Every writing box that changed size, corpus-wide**, on the tree this
package actually wrote (`build/html`, re-derived from source): the 4 comb
input insets above, plus 17 plain-field writing-box changes across 9 more
forms, all the same `intrusions()` precision fix: 1604f-2018 p1c160 top
0.91→0pt (grew); 1600wp-2010 p1c62/p1c67/p1c68 top 0.74→0.25pt (grew) and
p1c63/p1c64 top 0.74→0.7471pt (+0.0071pt, further trimmed); 1604cf-2008
p1c20 top 1.63→1.6742pt, p1c31/p1c32 top 1.15→1.1942pt, p1c316-i0/i1 top
35.74→35.785pt (all further trimmed, +0.0442–0.045pt); 1706-2018 p2c179-i1
left 14.56→14.35pt (grew) and p2c183-i top 0.41→0pt (grew); 1800-2018
p1c186-i top 1.13→0pt (grew); 2200an-2018 p2c245-i/p2c253-i right
20.3→20.1pt (grew); 2200p-2020 p2c376-i/p2c390-i left 11.96→11.82pt
(grew); 2200t-2022 p3c109/p3c116/p3c123 left 19.73→19.5724pt (grew);
2550m-2007 p1c87/p1c88 top 0.86→0.72pt (grew); 2551m-2002
p1c34/p1c39/p1c44/p1c49/p1c54 right 1.44→0.9014pt (grew). Every "grew" case
is a coarse over-trim recovered by measuring the actual offending glyph
instead of the whole run's declared descent; every further-trimmed case
closes a genuine gap between a face's declared descender and a specific
glyph's real outline, all under 0.05pt. None crossed `FIELD_MIN_SIZE_PT`;
input count is unchanged at 45,546.

**A real source-level mutation**, so F224's refusal is not repeated:
`make_fixtures.ink_trim_comb_row` (new, `fixtures/rules.pdf`) builds a comb
whose divider ticks are confined to a band below the row's own top wall —
spanning the row's full height reads to the lattice grid-building pass as
a genuine column boundary and splits the row into separate field cells
instead of one comb, the mirror, on the divider side, of
`comb_writing_rect`'s own "a tick is a guide mark under the box, not the
box" — with "Telephone No." set 1.0pt above it, whose 'p' measures a real
0.242pt top-clear by default. `prove_fixtures_fail.mutate_ink_trim_comb`
raises `INK_TRIM_CAPTION_DRIFT_PT` 0→2.0pt, lifting the caption clear;
`prove_ink_trim_comb` (new, run outside CASES/CONTRACT_ONLY the way
`prove_row_number`/`prove_comb_band_reunification`/`prove_signature_rule`
already are, since this is a `lattice.py`/`emit.py` decision with no new
extract-level primitive) confirms the claim flips from a positive
top-clear to exactly 0.0. `prove-fixtures-fail: PASS over 19 source-level
mutations` (unmoved) plus this new one, run separately and also passing.

**Pins.** `extract.FIXTURE_FIXTURES["FIXTURE-RULES"]` and
`comb_referee.AUDIT_DEPENDENCY_SHA256["tools/formgen/extract.py"]` moved
(`fixtures/rules.pdf` gained `ink_trim_comb_row`'s new shape; no
extract.py check, count or tolerance moved).
`comb_referee.EXPECTED_HTML_STRUCTURE_SHA256` re-pinned for the 12 forms
whose `build/html/<slug>.html` bytes moved (1600wp-2010, 1604cf-2008,
1604f-2018, 1701ms-2024, 1706-2018, 1800-2018, 2200an-2018, 2200p-2020,
2200t-2022, 2316-2021, 2550m-2007, 2551m-2002); the other 41 are
byte-identical. Comb censuses (`EXPECTED_COMBS`, `EXPECTED_COMBS_BY_SLUG`,
`EXPECTED_RETAINED_SUBJECTS_BY_SLUG`) do not move — no slot rectangle,
divider or slot count changes anywhere in the corpus.

**Determinism**: two `batch.py` regenerations over the full 53-form corpus
produce byte-identical `build/html`, `build/ir`, `build/layout` and
`forms/` trees (tree digests match exactly: `build/html`
`b49e98dad6bb…`, `build/ir` `0a2ce4fb31f9…`, `build/layout`
`b20ed08cfd28…`, `forms` `e4bd8a7e859d…`).

**Standing checks.** `extract.py --self-test` (7 pinned PDFs) and
`--self-test --fixtures` (6 fixture PDFs, re-pinned); `lattice.py --ir
build/ir/2551q-2018.ir.json`; `emit.py --self-test` (all assertions,
including two rewritten to state the new correct behaviour instead of the
old one this closes — the comb-exemption test and the
`intrusions`/line-box test); `comb_referee.py --self-test` and a full run
(`EXPECTED_HTML_STRUCTURE_SHA256` matches on all 53); `gate.py --self-test`;
`tab_check.py` (53/53 green, blue 5); `validate_tree.py`;
`fixtures/prove_fixtures_fail.py` (20 source-level mutations, up from 19).
`review-findings.json`: F227 fixed.


## W5 — 6 of 19 source-topology-unevaluable comb offenders decided (agree); two mechanisms extended and shipped, two implemented then refused

**Measured 2026-08-13, worktree `wt/w5-referee`, base `ae50f7ee`.** No producer
file touched (`extract.py`, `lattice.py`, `emit.py`, `fonts.py`, `guides.py`,
`correct.py`, `batch.py` all byte-identical to base); only `audit.py` (the
locked judge, extended per its own review-bundle rule) and `comb_referee.py`
(one pin comment + hash) change. Two full-roundtrip `audit.py` runs (not
`--assertions-only`, so `comb_referee.py` can evaluate its own audit-evidence
binding) over all 53 forms: rules 100.0%/text 100.0% on every form, both
before mechanisms shipped and after -- the producer output is unmoved.
`inputs_over_printed_text` **2 forms/5 offenders, unmoved**, every assertion
other than `comb_slots_match_printed` **0 offenders, all 53 forms, unmoved**
(diffed assertion-by-assertion, form-by-form, against the pre-change tree),
comb censuses **4,587/4,557/30, unmoved** (`comb_referee.json`'s own totals),
tab-walk **53/53 green**, blue census **5, unmoved**. `comb_slots_match_printed`
moves **10 forms/19 offenders -> 9 forms/13 offenders**.

**Two mechanisms shipped, both decided-agree, both independently
cross-validated by `comb_referee.py`'s wholly separate Poppler-based
implementation (six of six new `agree` verdicts).**

- **Mechanism 1 (chromatic vector fill, 4 offenders closed):** the extractor
  refused a fill for being chromatic before ever looking at its geometry.
  `_rectilinear_fill_regions` (a pure refactor: the grey-fill path's own
  `re`/`qu` parse, now shared) is attempted regardless of colour; a chromatic
  fill that parses exactly rectilinear gets `_perceptual_luminance` (ITU-R
  BT.601, published coefficients, not invented) as its tone and its exact
  regions attached to the refused `UnsupportedVectorPaint`, never to the
  shared `page.paints`. `printed_compartments` locally promotes only the
  regions that intersect ITS OWN owner rect, via one `dataclasses.replace`
  that rebinds a local `page`, never the bundle-wide one every other
  assertion reads. Closes 2553-1999 p1c18/p1c20/p1c22/p1c24 (a shaded swatch
  1.32-5.04pt outside the comb's own writing edge, now correctly excluded
  from candidacy by the pre-existing `COMB_EDGE_PT` guard rather than
  blocking the whole comb).
- **Mechanism 2 (non-rectilinear vector stroke, 2 offenders closed):** the
  same "refused before its geometry is asked" shape. A bezier/diagonal
  stroke's own extrema-derived bounding rect -- pymupdf's `drawing["rect"]`,
  already exact, already computed -- now decides whether it can BE a divider,
  mirroring the pre-existing position-aware `text_hits` deferral exactly (own
  `stroke_hits` check, added to `deferred_reasons`). One that never straddles
  a divider the band's own rectilinear ink establishes no longer blocks; one
  that does still blocks, unchanged. Closes 1604cf-2008 p1c13 and 2550m-2007
  p1c13 (three decorative circles per comb, centred inside each digit
  compartment, never touching a divider line).

**One mechanism shipped, decided-nothing but genuinely deeper:**
mechanism 4 (no strict-majority topology, 2 offenders: 1604cf-2008 p2c73,
2551m-2002 p2c13). F064 lets a candidate band own a vertical mark's full
painted extent so a genuinely shared multi-row divider reads as one
continuous topology; here that extent is the WHOLE multi-row table column
(54-405pt), and the strict-majority test inside `_band_topologies` -- itself
untouched -- correctly fails against that denominator even though the row's
own 16.8pt slice is unambiguous. When the unclipped pass decides nothing and
blocks nothing, `printed_compartments` retries the identical unmodified rule
against the SAME band clipped to the claimed owner's own rectangle -- one
further, narrower measurement, not a different rule. Both offenders now
resolve a real divider topology (matching the layout's own claim) where
before there was none to reason about at all -- but a SEPARATE, genuine
U-frame-ownership problem (the shared column rail's own baseline search finds
a maximal frame spanning the whole page-wide table row, not this one column)
was sitting directly behind the topology blocker and is now what both report:
`why` moves from "no strict-majority topology" to "crops a wider source
U-frame," `failure_kinds` unchanged (`source-topology-unevaluable`), offender
count unchanged. This merges them into the SAME family as the 4 pre-existing
U-frame offenders (1604f-2018 p1c25, 2551m-2002 p1c82, 2553-1999 p1c87, and
1801-2018 p1c31's differing-interiors variant) -- 6 of the 13 remaining
offenders now share one root cause.

**Two mechanisms implemented, empirically validated against their own named
target offenders, then refused and fully reverted** -- both broke an
EXISTING, deliberately mutation-tested guard in `audit.py`'s own self-test,
which is exactly the fail-closed machinery this package is bound not to
weaken:
- A `_dominant_certified_topology`-style containment fallback
  (`compartment counts [3, 14]`/`[2, 4]`, 4 offenders: 2000-dst-2018 p1c109,
  2200a-2020 p1c62/p1c86, 1801-2018 p1c13) that admitted a topology whenever
  it strictly contained every other observed one, dropping the existing
  span-majority requirement. It resolved all 4 target offenders to the
  richer topology (matching the layout's own claim) -- and also made
  `printed_compartments`'s own self-test fixture "a minority richer slab
  stays competing and publishes relations" resolve, which the fixture exists
  specifically to refuse: a short, richer slab must never win on containment
  alone, only on a genuine majority of the measured band. Measured directly:
  containment-without-coverage cannot tell 2000-DST's real taller
  group-boundary ticks (2 of the SAME 14 dividers, drawn reaching higher, no
  new x-position) from a spurious short-lived divider that happens to be a
  positional superset by coincidence. No safe way to keep the real case and
  refuse the synthetic one was found in scope; reverted in full
  (`_topology_contains_positionally`/`_uniquely_containing_topology` and
  their call site removed, `_dominant_certified_topology` restored to its
  original inline closure, byte-identical).
- A multi-wall generalisation of `_frame_cut_at_source_walls` (the 4 "crops a
  wider source U-frame" offenders left open above) that searched `cuts` for
  any matching wall at the owner's x0/x1 rather than requiring one atomic
  adjacent-pair segment, so a claimed cell that owns one of its own thick
  interior walls (1604-CF/2551-M's own two-slot rows: a single 0.72-0.96pt
  divider, thick enough to register as a wall in its own right) could still
  be found. It resolved 1604cf-2008 p2c73 end to end (own scratch harness,
  confirmed against a hand-built owner certificate) -- and broke two
  mutation-tested self-checks, "border weight alone makes a wall" and
  "standing above the band alone makes a wall": a wall spuriously
  reclassified by either weakened predicate now let a claim silently
  resolve instead of correctly staying wide/uncropped, exactly the canary
  those two mutations exist to trip. Reverted in full, byte-identical to
  base.

**`comb-referee.py` now evaluates** (`audit_evidence_complete_forms` 53/53,
requires a full non-`--assertions-only` `audit.py` run so the roundtrip
closure is attested; the prior `--assertions-only`-only tree in `build/`
could not be evaluated by it at all, before or after this package).
`forms_ok` **23**, `forms_disagreement` **0**, `forms_unevaluable` **30**
(dominated by 116 corpus-wide lattice-ledger blockers unrelated to this
package -- e.g. 2550m-2007 stays unevaluable on 4 OTHER blocked combs even
though its own W5-closed p1c13 now agrees), `comparisons.agree` **4,514**,
`comparisons.unevaluable` **73**. All six mechanism-1/2 closures independently
cross-checked cell by cell against this wholly separate Poppler
implementation: 2553-1999 p1c18/p1c20/p1c22/p1c24, 1604cf-2008 p1c13 and
2550m-2007 p1c13 all read `comparison_status: agree`, `referee, lattice,
audit, and emitted agree`, with `referee_compartments` matching audit's own
count on every one. 2553-1999 p1c87 and 1604cf-2008 p2c73 (mechanism-4's
"decided nothing" pair, above) both read `comparison_status: unevaluable`,
`"audit published this subject as an offender with no printed topology"` --
consistent, not contradicted.

**No new review-findings.json entries.** Every offender this package moved
resolved decided-AGREE (matches the reviewed layout's own claimed slot
count); none resolved decided-disagree, so there is no producer defect to
file.

**Pin:** `comb_referee.AUDIT_PRODUCER_SHA256` re-pinned
(`2e8e4dff7389e1dc362124cd2e45a952e96b826f1ff128996ef58215dd084731`), cause
recorded inline at the pin. `EXPECTED_HTML_STRUCTURE_SHA256` **unmoved** --
`comb_referee.py`'s own hard-fail check on every one of the 53 slugs raised
zero `RefereeError`s, which is a byte-exact guarantee the mismatch check
alone (not a raw count) can give.

**Self-tests, all pass:** `extract.py --self-test` (24 checks, 24+24 probes),
`lattice.py --ir build/ir/2551q-2018.ir.json` (489/264 slots, unchanged),
`emit.py --self-test`, `comb_referee.py --self-test`, `tab_check.py
--self-test`, `validate_tree.py --self-test`,
`fixtures/prove_fixtures_fail.py`, `audit.py --self-test` (0 failures,
including 9 new checks: a luminance unit check against the BT.601 reference
values, and one positive + one boundary check per shipped mechanism, each
boundary check proving the mechanism can be WRONG and is caught --
mechanism-1's non-rectilinear chromatic fill still blocks, mechanism-2's
divider-straddling stroke still blocks, mechanism-4's still-fragmented
clipped row stays unevaluable). Two `batch.py` runs into scratch `--out`
directories (never `forms/`) produced byte-identical tree digests.
## W7 — is_one_boundary's fusion criterion, measured corpus-wide and refused (F222)

**Measured 2026-08-13, worktree `wt/w7-boundary`, base `24cdc6e7`. No producer
change; `tools/formgen/lattice.py` is byte-identical to base.** F222 left one
open question after W4b: closing the 23 real wall-crossing marks needs either
tightening `is_one_boundary`'s own fusion criterion or a new per-side ink
capture, and both were named as corpus-wide-reach mechanisms nobody had
measured. This package measured the first one and refused to ship it.

**Method.** Every FINAL v/h call `build_page` makes to `build_lattice` (the
two calls that actually determine `cell.x0/x1/y0/y1`, not the raw/legacy
pair) was instrumented over all 53 tracked `build/ir` files to capture
`fuse_boundaries`' own pre-fusion clusters. For every pair of clusters
currently fused into one boundary, measured directly (not inferred) whether
ANY ink anywhere on the page intersects the gap between them — the identical
"checked directly across the whole page width" method F222's own prior round
used by hand for the 1701Q and 1801 examples.

**Corpus measurement: 348 currently-fused pairs across all 53 of 53 forms
carry genuine (no-ink) paper in their gap.** The shape is universal, not
confined to F222's 6 named forms. 312 of those sit in one dense, continuous
population where the paper is strictly LESS than the larger of the two
sides' own rule thickness (ratio 0.197–0.931 against the wider bar), on 51 of
the 53 forms — matching the two positive fusions `is_one_boundary`'s own
docstring already names (0619E y=150.1, 1600WP x=357.0), BIR's own
deliberate "two rules read as one heavier line" design reused at page
headers, item separators and signature blocks corpus-wide with the same
1.08/1.2/1.32/1.44pt paper values recurring everywhere. 36 pairs, across 17
of the 53 forms, have paper AT OR PAST the larger side's own thickness
(ratio ≥ 1.0) — the shape F222's own 23 real sites carry (1701Q's 2.4pt gap
over a 1.44pt bar is ratio 1.667; 1801's 1.08pt gap over a 0.72pt bar is
ratio 1.5).

**One candidate discriminator was tested, and it is the function's own
documented intent, not a new tuning constant.** `is_one_boundary`'s docstring
already reads "a white core thinner than the bars around it ... reads as one
heavier line," but its code compares paper against the SUM of both sides'
own max thickness. Comparing against a SINGLE side's own max thickness (the
larger of the two) is what the docstring describes, and separates F222's own
two hand-carried cases exactly: h18/h19 (legitimate, 1.2pt/1.44pt, ratio
0.833, stays fused) vs h73/h74+h75 (F222's own canonical defect,
2.4pt/1.44pt, ratio 1.667, separates). Implemented only as a monkeypatched
substitution over a scratch copy run against the tracked `build/ir` —
`lattice.py` itself carries no edit — rebuilt all 53 layouts, and diffed
every cell against the committed `build/layout/*.json` cell-for-cell, W3's
own blast-radius method. Harness validated first: rebuilding with the
UNCHANGED `is_one_boundary` reproduces the committed tree exactly, **0
mismatches on all 53 forms**.

**Blast radius: 2,428 cells change across 17 of 53 forms** (0605-1999 67,
1600wp-2010 174, 1604cf-2008 545, 1606-2018 35, 1701-2018-conso 157,
1701ms-2024 212, 1701q-2018 8, 1707a-2021 8, 1801-2018 28, 2200s-2018 113,
2316-2021 163, 2550-ds-2025 138, 2550m-2007 180, 2551m-2002 117, 2551q-2018
175, 2552-2018 262, 2553-1999 46). **11 of those 17 forms are entirely
outside F222's own 6 named forms** and have never been checked by the live
Playwright census the prior round used to confirm the 23 real marks; the
change cannot be scoped to only the reviewed sites because the discriminating
quantity is a corpus-general property of every rule pair in the lattice.
Even inside the 6 reviewed forms the change is not surgical: 2550M's own
`p1c86` does not just move its bottom edge — the whole cell relocates,
`(360.48,813.6)-(484.46,828.58)` to `(302.47,812.16)-(346.32,823.92)`, and
gains a comb structure (`comb.cells` `None` → `4`) that did not exist
before, because separating one fused row boundary changes the DSU cell-merge
topology for the whole surrounding region, not only the one wall it was
measured against. 1701Q's own `p2c115`/`p2c116` do move the right direction
(`y1` `769.46` → `766.90`, off the printed wall they overran at `770.02`) but
land on the upper rule's own CENTRE, not its own far ink edge (`767.62`) — the
one site this candidate targeted is not landed exactly, only moved off the
defect.

**Refused, per this checkout's own calibration.** W3's Route A was refused at
166 cells/13 forms (its loosest tested bound) and 751 cells/24 forms (its
strictest), both exceeding F151's own 49-cell "too large to review at any
comparable scale" threshold. The one candidate measured here — **2,428
cells/17 forms** — exceeds W3's own worst rejected bound by more than 3x in
cell count, and unlike W3's Route A (which stayed inside the one form family
it targeted before generalising), 11 of its 17 touched forms have zero
relationship to F222's own evidence. No second candidate was tried: the
discriminator above is already the function's own documented design intent,
and narrowing it further to chase a smaller number would be exactly the
"tolerance widened until the pairs separate" move this task explicitly
forbids. `review-findings.json`: F222 stays open, resolution updated with
this measurement. No pin moved; no census, assertion, input count or
determinism digest changed, because no producer file changed.

## W4b — nine more signatures, and the wall-crossing overlay reds turn out to be 23 real (and not worth the fix) plus 4 correctly-shipped

**Measured 2026-08-12, worktree `wt/w4b-signature`, base `60a3ab16`.** Inputs
45,537 -> **45,546 (+9)**, tab-walk 53/53 green, blue census **5, unmoved**,
`inputs_over_printed_text` **2 forms/5 offenders, unmoved** (1604cf-2008,
2316-2021), `comb_slots_match_printed` **10 forms/19 offenders, unmoved**,
every other assertion **0 offenders, all 53 forms, unmoved**, comb censuses
**4,587/4,557/30, unmoved** -- all re-measured on the regenerated tree via a
full `audit.py --assertions-only` run, not carried over from a prior round.

**F221 case 1 closes 9 of the 12 named sites.** The caption and the vector-
drawn signature line are not drawn inside one cell, as the finding's own
prose said -- they straddle the SAME lattice wall the caption's own cell
shares with it, `SignatureLineBinding`'s own relation (caption below,
candidate above), not `RuledBlankWriting`'s (caption and rule share one
cell). The candidate above is refused today only because it is `kind=label`
(it also carries the jurat paragraph and an item number), never `field`.
`emit.SignatureRuleWriting` implements exactly that: a `label` cell owning a
VECTOR rule whose own thickness straddles its `y1` (`rule.y0 <= cell.y1 <=
rule.y1`, 0.0pt slack on all 9 real claims), with exactly one signature
caption in the cell sharing that wall naming it. Geometry is
`ruled_blank_field_box`, reused whole -- no new field-box function.
0605-1999, 1604cf-2008, 2550m-2007, 2551m-2002 and 2553-1999 give 8 rules
(F221's own case 1, closed exactly as scoped); 2316-2021's own item 55 is
reached by the identical shape without being told to look for it, closing
one of its four named sites as a direct consequence, not a special case.

**Every claim routes through both `BureauReservation` and `shading`**, and a
whole-cell test would have been wrong: 0605-1999's and 1604cf-2008's own
oversized jurat cell also prints an unrelated "Stamp of Receiving Office"
caption 300pt along the SAME cell. Measured directly before shipping the
narrower rect: a whole-cell reservation test refused BOTH real claims on
those two forms over a caption neither signature line sits under; the guard
is now asked over the claimed rule's own x-span, and both are correctly
admitted (0 of 9 real claims sit on a Bureau caption or a tint, either way).

**Browser-verified**, Tab from the page's first input, typed, read back: all
9 reachable at presses 84/85 (0605-1999), 249/250 (1604cf-2008), 90/91
(2550m-2007), 89 (2551m-2002), 79 (2553-1999), 175 (2316-2021); every input
computed `text-align: center`; bottom gap to the cell's own bottom border
0.48-0.63px (T5d's own bottom-seated treatment -- `seat_signature_line` is a
correct no-op here since `ruled_blank_field_box` already seats one line tall
on the rule, and is still called, not skipped, for the day a form leaves more
headroom than that).

**2316-2021's remaining 3 named sites are left open, filed as F226.** Two
(items 53/54, "Date Signed") each correctly own their own signature rule, but
the caption sits across a genuine intervening `blank` cell or an ungridded
gap the lattice made no cell for at all -- the exact "unconstrained adjacency
test bound the wrong cell" shape the task named as already-tried-and-rejected,
and this checkout's own F064/Route A history (W3) measured that a comparably
loosened lattice/adjacency relation reliably reaches cells nothing has
reviewed. The third (item 56) cannot bind under ANY cell-to-cell mechanism at
all: its own caption run is not assigned to any lattice cell on the page.

**F222 closes nothing and ships no code change -- the diagnosis is the
deliverable.** First, the finding's own question: every reader of a comb's
outer slot edges (`comb_slots_markup`, `cell_json`) already routes through
`comb_slot_edges`, which already prefers `writing_x0`/`writing_x1` over
`slot_x`'s own outer values (F208, verified live -- `p2c115-s0`'s own
rendered left edge is 355.734pt, matching `writing_x0`, not `slot_x[0]`).
`comb_slot_verdicts` keeps raw `slot_x` deliberately, documented at the call
site as a superset. No consumer was missed; F208 is complete on the X axis,
and the missed-consumer hypothesis this finding names is refuted.

Per-site verdict on all 27 marks (a live Playwright census against
`window.formgenFieldCensus()`, the SAME instrument the debug overlay itself
calls, plus the underlying rule/rail geometry): **23 are a real producer
defect** (1701Q's own 16, plus one each on 2550M/1801x2/2553x2/0605/2551M).
Every wall `boxAt` closes on is the claimed cell's own real border rule --
never a coincidental unrelated structure -- and the writing box genuinely
overruns it by 0.15-0.6pt past the overlay's own 0.25pt tolerance. Root cause
traced to source: `lattice.is_one_boundary`/`fuse_boundaries` fuses two
GENUINELY SEPARATE rules (1701Q: a real 2.4pt gap with no ink anywhere
between them across the whole page width; 1801: a real 1.32pt gap) into one
lattice line positioned in the gap, at neither rule's own true edge; the
fused boundary's reported thickness is the MAX of both rules', and
`cell.y1 - thickness` lands past the real wall by roughly the difference. A
genuine `lattice.py` row/column-boundary-fusion defect -- not F208's own
X-axis shape, and not comb-specific (7 of the 23 are plain text fields).

**Not fixed, deliberately.** The two candidate fixes -- tightening
`is_one_boundary`'s own fusion criterion (shared by every cell boundary in
the corpus; F064's own Route A precedent measured a comparably general
lattice correction moving 166-751 cells across 13-24 forms for ONE named
defect and still not fixing it) or adding a new per-side true-ink-position
capture to every cell's border (general-purpose, Y-axis, `comb_rails`' own
rail-ink shape but corpus-wide reach) -- both carry a blast radius this
package did not measure and cannot responsibly ship inside its own scope.
Every one of the 23 sites is a real, correctly-positioned input against the
rail a taxpayer actually sees; the overrun is in the writing box's own
unrendered clearance, never glyph ink (none of the 23 is an
`inputs_over_printed_text` offender).

**The remaining 4 (2550M's own `p1c203`, a 4-slot comb) are an overlay
over-report, not a producer defect.** `boxAt`'s non-strict wall search picks
`v228`, a genuinely separate, correctly-drawn vertical 0.96pt inside the
comb's own TRUE rail `v223` -- not this comb's own wall. `lattice.comb_rails`
already performs the wall-vs-tick discrimination this shape needs and
correctly identifies `v223` (`left_rail_ink` matches the rendered input's own
left edge exactly). `crossesCleanly` (F220) does not exclude `v228` either --
it cleanly spans the comb's own row height, a different failure shape than
F220's own. Not fixable on either side: the comb is already correctly
emitted, and FIELD_DEBUG_JS's own documented constraint -- it may not consult
the field layer's own containers -- forbids the overlay from breaking this
tie with comb-specific rail data. Left as-is, correctly.

**Standing checks and mutations.** `emit.signature_rule_corpus_assertions`
(53 forms, 9 rules claimed, 0 without a typing surface) and
`emit.signature_rule_writing_assertions` (paired synthetic positive/negative
fixtures proving the ownership test, the caption gate, the wrong-wall
refusal, the ambiguous-caption refusal and the Bureau/shading gate can each
independently fail) run every `emit.py --self-test`.
`fixtures.signature_rule_row` / `mutate_signature_rule` / `prove_signature_
rule` reproduce 0605-1999's own real "Title/Position of Signatory" refusal
on a rebuilt PDF (`prove_fixtures_fail.py`, run outside its own
CASES/CONTRACT_ONLY accounting, the `prove_row_number`/`prove_comb_band_
reunification` precedent).

**Pins.** `extract.FIXTURE_FIXTURES["FIXTURE-RULES"]` and
`comb_referee.AUDIT_DEPENDENCY_SHA256["tools/formgen/extract.py"]` moved
(`fixtures/rules.pdf` gained `signature_rule_row`'s new shape; no
extract.py check, count or tolerance moved).
`comb_referee.EXPECTED_HTML_STRUCTURE_SHA256` re-pinned for the 6 forms
whose `build/html/<slug>.html` bytes moved (0605-1999, 1604cf-2008,
2316-2021, 2550m-2007, 2551m-2002, 2553-1999); the other 47 are
byte-identical, verified directly (`comb_referee.py`'s own run: `forms_error:
0`, comb censuses 4,587/4,557/30 match exactly). `review-findings.json`:
F221 fixed (9/12, residue filed as new finding F226, left open with its own
measurement); F222 measured and left open, no code changed; F226 filed and
left open.

**Determinism**: two `batch.py` regenerations over the full 53-form corpus
produce byte-identical `build/html`, `build/ir`, `build/layout` and `forms/`
(tree digests match exactly: `build/html` `c99cde01be21…`, `build/ir`
`f8cc40faa735…`, `build/layout` `84bddb4e4040…`, `forms` `dd80a38813e9…`).

## W3 + W4 — the abort was the deliverable, and the blue census became readable

**Gate r55: 10/13**, determinism byte-identical (`b3c72753a362`), open
blocker+major **6 -> 3**, zero blockers. Inputs 45,487 -> **45,537**, tab-walk
53/53 green, **blue census 108 -> 5**.

**W3's most valuable output is a refusal.** F064's recorded general fix --
bound a lattice line by the x-extent of the ink that induces it -- was measured
at three tolerances over all 53 forms before any behaviour changed. At the most
permissive (30pt) it moves **166 cells across 13 forms AND STILL does not
resolve item 8A**; at the strictest (0pt) it moves **751 cells across 24 forms
and regresses an unrelated 12-slot money field on 1707's own page 1**. Both
dwarf the 49-cell scale that already needed a 31-cell correction in W2. The
"obvious" fix would have moved hundreds of unreviewed cells and not fixed the
thing it was for.

Route B shipped instead: a comb subject with no rectangular owner gets its own
rectangle, bounded only by existing lattice positions, absorbing verified-empty
cells only and refusing any wall that is not one of the comb's own dividers.
Operator-verified in a browser: 1707-2021 item 8A has **25 focusable
compartments**, "SPECIFIED ANSWER" types straight across, first compartment at
Tab press 37. It generalises without special-casing -- 1707a-2021 25/25,
2551m-2002 4/4 -- so 3 forms change and 50 are byte-identical, and the
referee's retained floor falls **33 -> 30**, further than the 32 projected.
W3 also caught a bug in its own fix that only the tab-walk could see: the
reunified cell was appended rather than inserted at its `(y0,x0)` position, so
it tabbed dead last (`red-order=25` on both 25-slot forms).

**W4 made the missing-input census usable: 108 marks -> 5.** F220's mechanism,
measured in a live page rather than taken from the finding's prose: `boxAt`
closed boxes on wall members flush with the box at one end while running far
past the other (1604CF's data-table dividers start at a coincidental crossing
rule then run 108.96pt further through real rows). The fix is applied only from
`allBoxes`' strict search, never from the per-input lookup, so **it can only
narrow the vacant population by construction**. Two regressions were found and
reverted before landing: an unscaled margin broke 9,421 real inputs (comb
ticks' normal sub-point overshoot read as asymmetric), and applying the test to
top/bottom edges broke every comb's edge compartments (shared rails are
legitimately flush by design). Safety proven the right way: `fits/small/over/
unboxed` totals are byte-identical before and after on all 53 forms
(43,731/604/27/240) -- **zero real inputs reclassified**.

F214 fixed and proved with a fixture reproducing its exact evidence shape (the
misplaced field is now the one graded red, not the fields it pre-empted), plus
a genuine focus-trap fixture proving the cap path the tool's "never silently
truncated" claim depends on. F217 was proved LIVE before being fixed -- the old
computation mismatched by 3 cells on 1701. F215, F216, F218, F219 closed.

**Two process notes.** W4 re-pinned all 53 hashes from its own regenerated
tree but committed only its six source files, leaving tracked `forms/` running
the previous overlay -- caught by measuring the blue census after the merge and
getting 108 rather than the reported 7. And merging W3 over W4 conflicted on
exactly the files two concurrent packages must both touch: the pins were
**re-derived from the merged tree**, not taken from either branch, because a
merged pin is a pin from neither.

## W3 — a comb band gets a rectangle instead of the general walk getting a new rule (F064 closed)

**Measured 2026-08-12, worktree `wt/w3-lattice`, base `986fe767`.** F064: item
8A's 25-slot comb on 1707-2021 page 1 was correctly recognised (rails, pitch,
divider count all measured) but suppressed with zero inputs, because the
general cell walk cuts its band at y=343.44 -- a false row boundary two Yes/No
checkbox bottom edges (x 132.96-145.32 and 190.80-203.16, both entirely left of
the comb's own rail at x~233.7) induce despite neither ever reaching the
comb's own x-range, and a page-wide x-coincidence between the comb's own
dividers and unrelated ink elsewhere then fragments what the false cut leaves
into eight further pieces.

**Route A (rework the general lattice walk so no line's reach exceeds the ink
that induces it) was measured corpus-wide and refused, honestly, per the
package's own abort clause.** A candidate fix bounding a lattice line's
coverage span to ink genuinely local to its own defining rules -- rather than
any same-centre ink anywhere on the page, the mechanism this whole defect
traces to -- was tested at three tolerances. Even at the most permissive
bound tested (30pt): 166 cells move across 13 of the 53 forms, and item 8A
still does not resolve (the false row cut and the row-run "exact span match to
stack" requirement are a second, independent obstacle the coverage fix alone
does not reach). At the strictest bound (0pt, near-total): 751 cells move
across 24 forms, AND it regresses an unrelated field on 1707-2021's own page 1
(a 12-slot money field collapses from three cells into one non-functional
`mixed` cell). Both numbers dwarf F151's own 49-cell threshold for "too large
to review at any comparable scale." Route A is abandoned on this evidence, not
attempted.

**Route B -- named by the same finding -- ships.** `lattice._reunify_comb_band`
gives a legacy comb subject with no current rectangular owner its own
rectangle: bounded only by lattice positions that already exist elsewhere on
the page (a rail's true position is found by walking outward from the comb's
own divider run and accepting the first candidate whose OWN locally-joined
ink -- never ink merely sharing its x from anywhere else on the page, Route
A's own bug turned into a discriminator -- actually covers the band's height),
never inventing a new column or row. Every current cell the rectangle would
touch is either absorbed whole (verified empty: no comb, no printed ink, no
`source_owned_comb_frame` certificate) or trimmed on exactly one side (never
split into an L-shape); the resulting rectangle must not cross any wall that
is not one of the comb's own dividers, must not swallow printed ink (checked
directly against `text_runs`, since text is not yet bucketed onto `cells` at
this point in the pipeline), and -- the load-bearing guard a synthetic
self-test found -- must never touch a cell whose own DSU component was
already fully occupied, because `source_owned_comb_frame` already had the
chance to certify or explicitly refuse that exact shape and reunification
must never re-litigate a refusal it cannot see the reasoning behind. A final
topology pre-check (`same_boundary_topology` against the current pass's own
independently-anchored dividers, not the legacy comb's own list, which can
itself include a rail miscounted as a divider) refuses any candidate that
will not end up owning the subject's own exact divider set -- caught live
mid-package: an early draft merged 2200C-2018's `p1c13`, a genuine
already-correct 2-slot comb one divider-run-step from a different 4-slot
subject, for zero gain.

**1707-2021 item 8A verified in a real browser, the user's own method:**
`page.goto(file://…)`, Tab from the page's first input, typed and read back
in the first, middle and last compartment. All **25 compartments reachable**,
at tab presses **349 through 373** (25 consecutive presses, one per slot, in
order 0-24). The same method on the two forms Route B generalises to
(**never special-cased to 1707-2021**): 1707a-2021's own matching item-8A
shape, 25/25 compartments at presses 507-531; 2551m-2002's own 4-slot comb
(`p1c103`, a different retained subject from the same form's own four), 4/4
compartments at presses 12-15.

**Full corpus delta, measured on the tree the corpus diff and the referee
both scored: exactly 3 forms change, 50 are byte-identical.** 1707-2021
(item 8A, 25 slots), 1707a-2021 (its own matching shape, 25 slots) and one of
2551m-2002's four retained subjects (a 4-slot comb, `p1c103`). Every other
retained subject on every other form is left exactly as it was: each fails
one of reunification's own checks (already has a current-cell owner with a
different defect; the candidate rectangle would cross a wall not among the
comb's own dividers; would absorb a cell a certificate or an already-resolved
comb owns; or would swallow printed ink). Zero regressions: comb-cell and
comb-slot counts move on no other form, corpus-wide, verified by re-deriving
`lattice.build_layout` over every one of the 53 cached IRs and diffing
against the committed `build/layout/*.json` cell-for-cell.

**Census, measured directly (not from the assertion suite, which does not
carry these numbers):** `EXPECTED_COMBS`/`EXPECTED_COMBS_BY_SLUG` do **not**
move -- **4,587**, per-slug unchanged on all three forms -- because
reunification never adds or removes a ledger entry, it changes three
existing ones' state and subject_key. `comb_subjects_active`
**4,554 -> 4,557**; `comb_subjects_retained_unresolved` **33 -> 30**
(1707-2021 leaves `EXPECTED_RETAINED_SUBJECTS_BY_SLUG` entirely, 1 -> 0;
1707a-2021 2 -> 1; 2551m-2002 4 -> 3 -- **not** the "33 -> 32" the package
brief itself projected for a single-subject fix, because the mechanism is
corpus-general by construction and two more real, equally-evidenced
resolutions were the honest result of measuring it that way rather than
hand-limiting it to the named form). Input count: **+54** (25 + 25 + 4, one
`<input>` per newly-owned comb slot, the same identity every prior package in
this file measures its own delta by).

**Assertions, measured via a full `audit.py --assertions-only` run (53 forms,
verified not truncated): unmoved.** `inputs_over_printed_text` **2 forms / 5
offenders** (1604cf-2008, 2316-2021) -- byte-identical to the pre-existing
baseline. `comb_slots_match_printed` **10 forms / 19 offenders** -- also
byte-identical; this comb was suppressed, not previously counted as an
offender in that assertion either way, so its resolution moves neither
number. Every other assertion (`money_boxes_have_inputs`,
`rules_below_guide_cut`, `run_colour_matches_ir`,
`reflow_rate_without_description`, `image_transform_applied`,
`no_invented_codepoints`, `inputs_span_no_printed_divider`,
`printed_box_peers_all_fillable`) holds at 0 offenders, all 53 forms.

**Corpus tab-walk 53/53 green**, including both re-checks after a bug this
package found and fixed in itself: the reunified cell was first `cells.append`-ed
at the END of the list, so it tabbed dead last on the page instead of where it
prints -- `tab_check.py` caught it directly as `red-order=25` on both
1707-2021 and 1707a-2021 (one press per slot in the wrong position). `cells`
is reading order (`(y0, x0)`, the same key F209 already established for
this exact reason); the new cell is now **inserted** at its own sorted
position instead of appended, and both forms are 53/53 green with
`red-order=0` after. **Blue (`vacant`) census: 108 -> 106** (measured via a
fresh `tab_check.py --json` run over the corpus) -- **fell, did not rise**:
exactly the two forms whose item-8A comb used to be a "printed compartments,
zero inputs" blue box and now is not (1707-2021 and 1707a-2021 both measure
0 vacant after; 2551m-2002 still measures 3, its own three untouched
retained subjects, unmoved).

**Self-tests: all pass.** `extract.py --self-test` (7 pinned PDFs, 24 checks,
unmoved). `lattice.py --self-test --ir build/ir/2551q-2018.ir.json` (489/264
slots, unmoved -- 2551Q carries none of this package's own subjects).
`emit.py --self-test` (every corpus-wide assertion, including the five other
producers' own comb/field mechanisms, pass unchanged). `comb_referee.py
--self-test`. `gate.py --self-test`. `validate_tree.py` (7/7). `fixtures/
prove_fixtures_fail.py` (19 source-level mutations + `prove_row_number` +
the new `prove_comb_band_reunification`, all pass).

**A real source-level mutation, run outside `prove()`'s own
CASES/CONTRACT_ONLY accounting** (comb-band-reunification is a `lattice.py`
decision with no new extract-level primitive, the identical shape F151's own
`prove_row_number` is): `make_fixtures.comb_band_reunification_row` builds a
bordered row whose comb's own left rail is drawn only from mid-row down (like
1707-2021's own v255/v256, continuing an existing border rather than hanging
free) at `COMB_BAND_REUNIFICATION_NOTCH_SIZE_PT` 0.0pt -- every built
fixture -- where the comb resolves through the ordinary path,
`active_resolved`, no retained subject at all. `mutate_comb_band_reunification`
draws a small bordered notch box inside the row instead (12.0pt): the row's
own DSU component stops being fully occupied, F064's own `no-rectangular-
owner` ledger state appears on a REAL rebuilt PDF, and reunification
correctly declines to absorb it (the notch's own two internal walls match
none of the comb's own divider positions) -- proving the mechanism's own
central safety discriminator can fail, on the source, not on an in-memory IR
edit.

**Pins moved, each with its cause named at the pin.** `LATTICE_PRODUCER_SHA256`
(`comb_referee.py`) -- `lattice.py` gained the mechanism, then gained the
reading-order fix. `AUDIT_DEPENDENCY_SHA256["tools/formgen/extract.py"]` --
`FIXTURE_FIXTURES["FIXTURE-RULES"]`'s own sha256 moved (`fixtures/rules.pdf`
gained the new shape); no extract.py check, count or tolerance moved.
`EXPECTED_HTML_STRUCTURE_SHA256` re-pinned for 1707-2021, 1707a-2021 and
2551m-2002 only; the other 50 are byte-identical, verified directly.
`EXPECTED_RETAINED_SUBJECTS_BY_SLUG` **33 -> 30**, cause named inline (above
and at the pin). `review-findings.json` F064 closed.

**Determinism:** two full `batch.py` regenerations over the corpus (three,
counting the reading-order fix's own re-run) produced byte-identical
`build/html`, `build/ir`, `build/layout` and `forms/` trees each time,
verified by sha256 digest and by `git diff` reporting no changes on a
same-code re-run.
## W4 — the tooling backlog closes (F214-F220)

**Measured 2026-08-12, worktree `wt/w4-backlog`, base `986fe767`.** Seven
findings closed, none of them a field decision: `tab_check.py --self-test`
passes 26 checks including three new fixtures, the corpus tab-walk stays
**53/53 green**, and the blue vacant census falls from **110 to 7**
corpus-wide with **fits/small/over/unboxed byte-identical before and after on
every one of the 53 forms** (43,731/604/27/240). Input count on this tree's
own seeded corpus is unmoved at **44,602** before and after (see the note
below on why that is not 45,487).

**F214 (major) — the tab-walk blamed the wrong field.** `compute_verdicts`'s
`running_max = -1` sentinel let the DOM-first-reached input pass
unconditionally, so when that input was NOT the reading-order-first one, it
was graded green and the fields it pre-empted were graded red-order — the
inverse of the truth. Fixed by grading only the first counted entry against
`rank == 0` instead of the general `r >= running_max` rule; every later entry
is unaffected. Proved by a new fixture reproducing F214's own evidence shape
(DOM order [rank 1, rank 0]): the misplaced field is now the one graded red.
Two further self-test gaps closed: a genuine focus-trap fixture proves
`terminated_by == "cap"` and a field the trap prevented from ever being
reached is red-skipped, not silently dropped; the self-test browser context
now matches the corpus run's `device_scale_factor=2`.

**F215 (minor) — latent silent false green.** `build_expected` now raises
`DuplicateInputIdError` the instant a second input claims an id, instead of
silently collapsing two fields into one verdict slot; `walk_form` turns that
into a named hard per-form failure. Latent on the shipped corpus (no
duplicate ids exist today) — the guard's value is failing loudly the day one
is introduced.

**F216 (minor) — stale CI comment.** `.github/workflows/formgen.yml`'s
"KNOWN RED, BY DESIGN" note (F209, closed two commits after the comment was
written) is replaced with the true, current state: 53/53 green, no
tolerated red.

**F217 (minor) — checker-of-checkers hole.** `field_assertions`'s
independent re-derivation of `FieldPlan`'s fillable set never wired
`checkbox_squares`, `knockout_specify` or `row_numbers` into its own
`field_verdict` calls — latent on the pinned self-test form (2551Q) by
coincidence for two of the three (its one `knockout_specify` claim is also a
`ruled_blanks` claim, checked first). Proved real by reproducing the OLD
computation against 1701-2018 (407 expected vs 404 old-style fillable, a
genuine 3-cell mismatch) before fixing it. All three now wired the same shape
`ruled_blanks`/`signature_boxes` already had.

**F218 (minor) — guard asymmetries in `field_verdict`.** The ruled-blank and
checkbox-square branches now route through `BureauReservation`, the same
guard the signature-box branch already took deliberately (zero live cases,
closed by construction regardless). The ruled-blank branch's deliberate
non-consultation of `DecorativeShading` — correct, verified by measurement
(of 48 underscore rules on tint, 41 sit in a white knockout and 7 are genuine
write-on lines) — is now documented at the branch and captured by a new
standing assertion: 27 shaded ruled-blank claims corpus-wide, 0 lost their
typing surface.

**F219 (minor) — band-blob mirroring.** `SignatureLineBinding`'s inline
`text-align:center` is now mirrored into the growable-band runtime blob
(`field_json` gains a sparse `centered` key; `BAND_JS`'s `fieldMetrics` gains
a `slotIndex` parameter and sets `textAlign` from it). Zero live cases,
exactly as F219 predicted — the band-data JSON blob is byte-identical across
all 53 forms before and after — proved structurally instead, by two
synthetic `FieldPlan` checks and a source-text assertion.

**F220 (major) — the overlay closed boxes on faces that do not span them.**
`FIELD_DEBUG_JS` gains `crossesCleanly`: a vertical (L/R) wall member is
refused if it is flush with the box's own edge at one end (within
`Math.max(TOL, box span)`) while running clearly past the other — the shape
of a rule belonging to a taller structure that merely grazes this box, not
one drawn for it. Measured directly in a real Chromium page rather than
re-derived from prose: 1604cf-2008's phantom closes on two real data-table
dividers whose ink starts EXACTLY at the box's own top (0.00pt off — the same
rule that defines it) and runs 108.96pt further down through the real rows
beneath. Applied only in `strict` mode, from `allBoxes`' own candidate search
alone, never from the per-input lookup that classifies a real input's
verdict — provably safe, not just careful, since any box a real input
resolves to is re-added to the candidate set from that non-strict lookup
regardless of what `allBoxes` found. Two regressions found and reverted
before landing: an unscaled margin cost 9,421 real inputs their box
corpus-wide (a comb tick's own few-tenths-of-a-point overshoot read as
"asymmetric"); applying the same check to T/B rejected every edge
compartment of every comb in the corpus (a comb's shared rail is, by design,
flush with its own edge on one side). `HTML_RUNTIME_SCRIPT_SHA256` and all 53
`EXPECTED_HTML_STRUCTURE_SHA256` (`comb_referee.py`) re-pinned for this and
F219 together; comb census unmoved (4,587/33/4,554); determinism verified
(two independent 53-form regenerations, byte-identical).

**Why input count on this tree is 44,602, not 45,487.** STATUS.md's own
r54/W2 entry above states 45,487 measured AT commit `986fe767` — this
worktree's own base — but that number was measured on a corpus regenerated
fresh from the six pinned source PDFs on `gol/form-correction`. This
worktree's `build/ir`/`build/layout`/`build/fonts` were seeded directly
(no PDFs available to re-extract from), and the seeded corpus's own baseline,
measured before any change in this package, is 44,602 — not a number this
package moved or could account for. What this package proves instead, and
what its own acceptance criterion asks for, is that the count is IDENTICAL
before and after every fix here (44,602 == 44,602): this package changes no
field decision, regardless of which absolute baseline the seed started from.


## W2 — the last blocker closes, and the flag mattered more than the fix (F151)

**Gate r54: 10/13**, determinism byte-identical (`c6d61584d081`), open
blocker+major **7 -> 6 and ZERO blockers remain**. Inputs 45,468 -> **45,487**,
tab-walk 53/53 green, blue census 108 unmoved.

F151's Schedule D Description cells are fillable. P2's row-number rule landed
as specified — a `label` cell sharing a row with a field cell, holding only a
short numeral, with a trailing blank clearing the form's own `line_width_pt`
and room to write. **P2 recorded a caveat that no longer applied**: it said
F151 could not close because its Schedule C half was unseparable. F151 was
later NARROWED and that half REFUTED (those cells hold 88-94% of their own
pre-printed category names), so Schedule D alone closes it. Reading both
records against each other was the difference between attempting this and
skipping it.

P2 measured 296 -> 56/23 on the r38 tree; this tree gives 61/13, drift fully
explained by ~35 intervening commits that reclassify `label` cells, of which
12 were already fillable — **49 net-new**. Nothing was tuned toward 56.

**The implementing agent's flag was worth more than its patch.** It promoted
1621-2019 p2's "Seq. No. (A)" column and, rather than hand-excluding it, said
so and left it for review. Rendering it settled the question in one look: a
grey band whose 1..5 the Bureau prints. Measuring the whole promoted set found
**31 of 49 sit on >=70% tint** — printed row indexes across 7 forms, not
writing surfaces.

The cause is the third instance of one shape: the new branch returns before
reaching the refusal the ordinary field path applies a few lines below ("a
blank the source shaded is not a blank either") — the same ordering asymmetry
**F218** filed against the ruled-blank and checkbox branches. It now asks the
existing shading gate; no new constant, no form-code special case, the
discriminator is the source's own tint. `row_number_corpus_assertions` asserts
**both directions** so the exclusion cannot rot into a silent skip: 31 shaded
cells, 0 leaked.

Browser-verified: `1701-2018-conso` p2c132/136/140/144 type and read back;
1621-2019 p2c16/23/30/37/44 have zero inputs. An agent also found and fixed a
real defect mid-package — its first cut let a writing box overlap foreign
printed ink from a neighbouring cell (0605-1999 p1c81), which
`inputs_over_printed_text` caught at 2/5 -> 3/6; requiring the band be free of
foreign ink page-wide restored 2/5 and 0605 correctly gains nothing.

## W2 — the row-number rule (F151 closed, the last blocker)

**Measured 2026-08-12, worktree `wt/w2-row-number`, base `2b3e20c8`.**
Implements P2's own measured rule exactly: a `label` cell sharing its row with
a `field` cell, holding ONLY a bare numeral (`^\d{1,3}$`), earns the paper
beside it when the trailing blank clears the form's own `line_width_pt`
(`lattice.min_fillable_line_metrics`, the sliver rule's own metric) at 1.0x --
no new constant. `emit.py` gains `RowNumberWriting` / `row_number_band` /
`row_number_field_box`, the fourth member of the `RuledBlankWriting` /
`CheckboxSquareWriting` / `KnockoutSpecifyWriting` family.

**The number moved from P2's own measurement, and the reason is named rather
than papered over.** P2 measured 296 candidates -> 56 across 23 forms on the
r38 tree (2026-08-10). Re-derived on this tree by the new standing self-test
(`emit.row_number_corpus_assertions`, run by `emit.py --self-test`, corpus-wide
every run): **61 cells across 13 forms.** ~35 commits landed between r38 and
this tree that are each capable of reclassifying a `label` cell -- F148/F149
(ruled-blank), F210 (checkbox-square), F211/F212 (signature-box), F206
(knockout-specify), and several lattice-level ink/wall/pre-printed-text fixes
-- fully accounting for the drift. Of those 61, **12 are already fillable via
`RuledBlankWriting`**, which landed after P2's own measurement (2200m-2018's 4
cells; 1702mx-2018c's p2c278/p2c280 and p4c196/198/200/202/204/206), so the
**NET NEW gain this package ships is 49 cells across 11 forms**: 1600-pt-2018
(5), 1600-vt-2018 (5), 1621-2019 (5), 1700-2018 (4), 1701-2018 (2),
1701-2018-conso (4, the four Schedule D anchors this closes F151 for), 1702mx-
2018c (4), 1702mx-2018c-attachment (8), 1702rt-2018c (8), 2200a-2020 (2),
2200p-2020 (2).

**The four Schedule D anchors** (`1701-2018-conso` p2c132/p2c136/p2c140/
p2c144) verified in a real Chromium page: `page.goto(file://…)`, Tab pressed
437/441/445/449 times from the first input to reach each, typed
"Contribution to accredited NGO" into each and read the value back correctly.

**Excluded, by construction, verified as a count.** All 228 narrow (13-16pt)
item-number boxes sharing a row with a field cell in this corpus (BIR's own
"12" inside a box barely wider than two digits -- P2's own population,
measured at 188 on the r38 tree, the same drift as above) stay refused: zero
overlap between that set and the 49 gained cells, checked directly. The
`1701-2018-conso` Schedule C cells `p2c97`/`p2c103`/`p2c109` (pre-printed
deduction category names, F151's own refuted half) are byte-identical before
and after.

**Measurement found a real defect and this package fixed it before shipping.**
The first cut of `row_number_band` trimmed only the candidate cell's own
leading ink and left the rest of `writing_box_clear_of_printed_ink`'s
"a glyph whose centre lies inside the box is not this function's problem"
rule unguarded for this promotion. On `0605-1999`, `p1c81`'s own row also
carries "  For the           Calendar           Fiscal" -- a checkbox caption
`assign_points` gave to a NEIGHBOURING cell, whose glyphs still physically
overlap p1c81's rectangle, the same shape CLAUDE.md already documents for
`printed_box_peers_all_fillable`, pointed a new way. The unguarded band
claimed paper that was not blank, and `inputs_over_printed_text` caught it
immediately (2 forms/5 -> 3 forms/6, 0605-1999's `p1c81` the new offender).
`row_number_band` now refuses any candidate whose trailing blank carries ANY
intrusion from ANY run's own ink, page-wide (`PrePrintedInk.intrusions`), not
just the candidate cell's own assigned run; 0605-1999's `p1c81` is correctly
excluded (raw claims 62 -> 61, and 0605-1999 drops out of the net-new list
entirely) and gains nothing. The 61/13/49-across-11 figures reported above
are all measured AFTER this fix.

Measured against the corpus, full regeneration (`batch.py` twice; determinism
confirmed against a third run into a scratch tree): `inputs_over_printed_text`
stays **2 forms/5 offenders** (1604cf-2008, 2316-2021), unmoved;
`comb_slots_match_printed` stays **10 forms/19 offenders**, unmoved; comb
censuses stay **4,587 subjects/33 retained/4,554 comb cells** (row-number never
touches a comb cell by construction, verified directly off `build/layout`).
Inputs **45,468 -> 45,520 (+52)**: 49 net-new cells, +3 more because three of
them sit in a growable band and are mirrored into that band's own `<template>`
blueprint row. Corpus tab-walk **53/53 green**. Blue/vacant census stays
**108**, unmoved.

A real source-level mutation was added rather than declined or parked in
`CONTRACT_ONLY` (a prior package's decline on this exact point is F224).
`fixtures/make_fixtures.py` gains `row_number_row` -- a synthetic bordered row
on `rules.pdf` splitting a bare-numeral label cell from a blank field cell --
and `fixtures/prove_fixtures_fail.py` gains `mutate_row_number` (narrows the
label cell 60pt -> 20pt, dropping the trailing blank under half of
`line_width_pt`) and a new `prove_row_number` routine that runs the actual
pipeline (`extract.extract` -> `lattice.build_layout` -> `emit.RowNumberWriting`)
over the clean and mutated fixture and asserts the claim flips. Deliberately
run OUTSIDE `prove()`'s own `extract.SELF_TEST_CHECKS`/`CONTRACT_ONLY`
accounting: row-number is a `lattice.py`/`emit.py` decision with no new
extract-level primitive of its own, so folding it into that table would
misname what it tests -- extract.py's own 24 checks are unmoved, only
`FIXTURE_FIXTURES["FIXTURE-RULES"]`'s own sha256 (and its dependent pin in
`comb_referee.AUDIT_DEPENDENCY_SHA256`) moved.

`EXPECTED_HTML_STRUCTURE_SHA256` (`comb_referee.py`) re-pinned for the 11 forms
whose `build/html/<slug>.html` bytes moved (all 53 verified to match the
regenerated tree). `review-findings.json` F151 closed.

## W0 + W1 — the sweep found more than the fix closed, and that is the honest direction

**Gate r53: 10/13**, determinism byte-identical (`9ceb064713d4`), inputs
45,467 -> **45,468**, tab-walk 53/53 green, blue census 108 unmoved, open
blocker+major **8 -> 7**.

**W0** put three reviewers over the PR's ~2,900 new lines and triaged the four
residual populations nobody had examined. Ten findings filed, four major, and
the most important is a defect in the INSTRUMENT: **F214** — `tab_check`'s
`running_max` starts at -1 so the first input reached is graded green
unconditionally, and the walk starts at the DOM-first input rather than the
reading-order-first one. When the first-focused field is itself the misplaced
one it is marked green and the correctly-ordered fields before it are marked
red. The form still fails, so 53/53 stands, but the page markers point a
reviewer at the wrong boxes — and the self-test cannot catch it by
construction. **F220**: 99 of the 108 remaining blue marks are phantoms;
1604CF `p1c33` is the clean case, a column-header row whose six interior
verticals stop 9.6pt short of its bottom rule while blue boxes sit *below*
where those verticals end, on faces that do not exist. **F221**: twelve more
unsignable signature areas on six forms. **F222**: sixteen comb-slot
wall-crossings on 1701Q, never investigated — and given F220 the
overlay-over-report hypothesis must be excluded before any producer change.

**A reviewer alarm was refuted by looking, not accepted.** 27 ruled blanks on
tint bands were flagged as possible write-on-shading surfaces. Of 48
underscore-origin rules sitting on a tint, 41 lie inside a white knockout the
sheet paints to clear a writing space; the 7 without one were rendered and
read — 1801 item 21 is "...to be paid on or before ____" with a visible white
box, 2200A/2200P are "Others (specify)" ATC lines. All correct. What remains is
that the precedence is undocumented (F218), not a filing-safety defect.

**W1** closed F206. Its measured payoff is one field, and that was checked
rather than taken on report: F206's own named defect `1801-2018 p2c261` was
**already fixed by T5c**, so the new mechanism nets exactly `1801-2018 p1c197`
— rendered before acceptance ("☐ Others *(specify)* ______" with a real
write-on line and nothing to type in), then tabbed to at press 254, typed into
and read back. The implementation needed a caption gate ("specify") that is NOT
in F206's recorded marker: the bare geometry selects 90+ cells, mostly false
positives. Disclosed, defensible under the caption-as-evidence precedent, but
the shipped rule is not the one F206 measured. F065 measured and left open (its
knockout is real but the caption run carrying the correctly-refused Arial
Narrow underscores covers it); F221 measured and left for W4 (the geometric
half reaches 6 of 12, none carries "specify", so the caption gate correctly
declines rather than improvising a second mechanism).

**Two process findings filed against ourselves.** **F224**: W1's mechanism
ships with no source-level mutation proving it can fail —
`prove_fixtures_fail` is still 19, unchanged; T5a hit the same situation and
solved it by extending the synthetic fixture instead of declining. **F225**: an
agent downgraded the machine-global `playwright` package 1.59.0 -> 1.58.0 on a
wrong diagnosis; the full tab-walk had run fine on 1.59.0 the same day, and
after the downgrade `chromium.launch()` printed the install banner, so the
change broke what it claimed to fix while diverging the machine from CI's pin
and from `audit.py`'s runtime attestation. Restored and verified.

## W1 — a knockout-over-tint band beside a "(specify)" caption is a field there too (F206 closed)

**Measured 2026-08-12, worktree `wt/w1-knockout-split`, base `b718994`.** F206's
own marker -- "the widest ink-free band whose topmost paint is a KNOCKOUT over a
decorative tint, at >= 1x the form's own line width" -- is real, but its own
prose under-specifies it: run bare against the corpus it selects 90+ `label`
cells, the large majority NOT writing surfaces (1604CF's 11-run table headers,
2550M's near-full-width declaration bands, 2553's "OT  0   1   0" ATC captions
with wide but non-writable margins). Two refinements, both forced by measuring
against F206's own named residue, narrow it to the real family: a fill counts as
"this cell's own tint/knockout" only past a MEANINGFUL clipped overlap
(`KNOCKOUT_FILL_MEANINGFUL_OVERLAP_PT`, 1.5pt, `extract.MAX_RULE_THICKNESS_PT`
restated, both axes -- excludes a 0.12-0.33pt graze from a neighbouring row's
own background), and a candidate cell's own text runs block by their WHOLE line
box, not per glyph (excludes an ATC code's inter-character gaps). A third gate,
not in F206's own prose but forced by the same measurement: the caption must
contain "specify" -- BIR's own free-text invitation, and the one thing 1801
p2c261 has that 1604CF's table headers and 2553's ATC captions do not.

`emit.py` gains `KnockoutSpecifyWriting` / `knockout_specify_band` /
`knockout_specify_field_box`, the third member of the `RuledBlankWriting` /
`CheckboxSquareWriting` family: a `label` cell's writing surface is a
sub-region, never a reclassification of the whole cell, and `lattice.py` is
byte-identical -- untouched. 23 cells across 11 forms qualify. **22 already had
an input from `RuledBlankWriting`**, `1801-2018` `p2c261` itself among them --
F206's own named example was independently fixed by T5c (gate r50,
2026-08-11), between F206's filing (2026-07-29) and this package, and this
package's own mechanism, run against it directly, agrees: same cell, same
verdict. **Exactly ONE cell gains a NEW input: `1801-2018` `p1c197`**, "Others
(specify)" (item 24), the same tint-caption / knockout-writing-surface
tri-partition as p2c261 but on a different row. Verified in a real Chromium
page: `#p1c197-i` reached by Tab at press 256, `#p2c261-i` at press 899, both
typed into and read back correctly.

**The 8 residue cells F206 named stay refused, two independent ways.** None
carries "specify" in its caption (checked directly, false in every case). And
independently, the pure geometric band -- caption gate bypassed -- measures at
or below the form's own line width for every one: 0605-1999 `p2c58` and
1600wp-2010's `p1c30`/`p1c33` clear no band at all past the meaningful-overlap
gate; 0605-1999 `p2c121` and 2553-1999's `p1c37`/`p1c42`/`p1c47`/`p1c52` measure
0.78x-0.91x. Neither test alone would be enough (the geometric ratio is close
for the 2553 cells; the caption test says nothing about geometry).

Measured against the corpus, full regeneration (`batch.py` twice, byte-identical
tree): `inputs_over_printed_text` stays **2 forms/5 offenders**, unmoved;
`comb_slots_match_printed` stays **10 forms/19 offenders**, unmoved; comb
censuses stay **4,587 subjects/33 retained/4,554 active cells** (guaranteed by
`lattice.py` being byte-identical, not merely measured). Inputs **45,467 ->
45,468 (+1)**. Corpus tab-walk **53/53 green** (118.2s). Blue/vacant census
stays **108**, per-form identical to the prior round -- `p1c197` was never in
it, the same F211 precedent (the overlay's `vacant` test skips any printed box
carrying text). A standing corpus-wide self-test
(`emit.knockout_specify_corpus_assertions`, run by `emit.py --self-test`)
re-derives the 23-cell claim set against every `build/ir` + `build/layout` +
`build/fonts` triple this checkout has and fails if any claimed cell lacks a
typing surface (`bands_checked > 0` asserted too); it also cross-checks the
restated `_min_fillable_line_metrics` (emit.py carries no import of lattice.py)
against `lattice.min_fillable_line_metrics` directly, on every form, so the two
cannot silently drift. Five paired positive/negative fixture assertions
(`emit.knockout_specify_writing_assertions`) prove the caption gate, the
meaningful-overlap gate and the whole-run-bbox ink block can each independently
fail, on synthetic geometry mirroring the named residue's own shape.

**F065 measured, not closed.** The knockout is real -- (471.82, 351.53, 583.66,
360.89), exactly this finding's own coordinates -- but the caption run carrying
"(please specify)" also carries the unresolved underscore sequence (F065's own
standing, correct refusal: unembedded Arial Narrow, no substitutable face), and
that run's own WHOLE bounding box (not per glyph, the same rule that keeps
2553's ATC captions out) covers virtually the entire knockout. No candidate band
survives. F065's other barrier is untouched.

**F221 measured, left for W4.** The GEOMETRIC half of the marker (caption gate
bypassed) reaches 6 of the 12 named signature areas -- 0605-1999 `p1c176`,
1604cf-2008 `p1c316`, 2550m-2007 `p1c181`, 2551m-2002 `p1c175`, 2553-1999
`p1c216`, 2316-2021 `p1c337` -- at 29x-52x their own form's line width: the same
tri-partition shape. But every caption is "I declare, under the penalties of
perjury..." or "Date Signed", never "specify", so this package's own caption
gate correctly never claims them -- a signature area needs
`SignatureLineBinding`'s bottom-seated, centred treatment, not a generic
left-anchored box, and building that here would have been the improvised second
mechanism the package brief forbade.

**Not attempted, and why:** extending `fixtures/prove_fixtures_fail.py` (the
`extract.py`-scoped PDF-mutation harness F210/F211 both extended) with a new
pinned real-world fact. Checked all 7 of `extract.py`'s pinned official PDFs
(2551Q, 0605, 1604E, 2550M, 2553, 2316, 1701) directly against the shipped
`KnockoutSpecifyWriting` -- none contains a cell it claims, so there is no real
fact in that corpus to pin a check against. Every geometric primitive this
mechanism reads (fill role/tone, glyph/run geometry) is already exhaustively
proven by `extract.py`'s existing checks (`tone`, general geometry fidelity),
so there is no new extraction-side contract for that harness to mutate; the
mutation proof lives entirely in `emit.py`'s own self-test, the same
paired-assertion idiom this file already uses for F147/F210/F211's own
`field_verdict` branches.

**Environment note.** `python -m playwright`'s pip package had drifted to
1.59.0 against a browser cache pinned at chromium 1208 (the node toolchain's
own 1.58.2 pin), which made `tab_check.py` fail to launch outright; downgraded
the pip package to 1.58.0 to match. Unrelated to any change in this package,
recorded because it changed the machine's Python environment.

## T5b + T5d — 54 signable boxes, 71 Bureau boxes untouched (F211/F212 fixed)

**Gate r52: 10/13**, determinism byte-identical (`1ebe225b54d1`), open
blocker+major **7 -> 6**, and `inputs_over_printed_text` **3 forms/6 -> 2/5** --
the first assertion improvement this session.

0619E's "For Individual:" / "For Non-Individual:" boxes took no input because
one ~9pt caption run at the top-left corner set `is_empty=False`, so the cell
was a `label` and `field_verdict` refused it. One caption run cost a 302x43pt
writing surface, and the overlay could not report it either: `vacant` skips any
printed box containing text, correct in general and blind here. 0620 `p1c87` is
the control -- same box, no interior caption, shipping an input all along.

**The measurement that mattered was the one taken BEFORE writing code.** The
obvious rule -- "a bordered box whose ink is confined to a top strip" --
selects **126 cells**, and they are two families: **54 signature boxes**
(exactly 2 per form on 27 forms) and **71 BUREAU-ONLY areas** ("Machine
Validation", "Stamp of Receiving Office/AAB", "Stamp of Authorized Agent
Bank"), plus one unclassified (`1600wp-2010 p1c93`, a table column header
"(8)", claimed by neither vocabulary and left alone). Shipping the naive rule
would have made 71 Bureau stamp boxes writable by the taxpayer.

**And the ordering trap under it:** `BureauReservation` (`emit.py:2481`)
already recognises those captions, but `field_verdict` refuses `kind != field`
at rule 2 while the Bureau check is rule 4b -- so for these cells the guard was
never consulted. A promotion written carelessly would have sailed straight past
it. `SignatureBoxWriting` calls `reservation.blocks(...)` explicitly before
returning True. Verified in a browser: 0619E `p1c139` ("Machine Validation")
has **0 inputs**.

T5d seats a signature line at its cell's bottom via `field_box`'s existing
inline inset and centres it with inline `text-align:center` -- no new CSS class
or stylesheet declaration, both of which the referee's allowlist rejects.
Target set 75 boxes / 43 forms: T5b's 54 plus 21 pre-existing signature
fields including `1701-2018 p1c125`, the strip the user typed into. Measured
in Chromium: input 13.42px tall in a 45.59px cell, 0.63px above the bottom
border (the border itself), `text-align: center`.

**A styling fix closed a correctness offender**: 1701MS `p1c287` grazed a
printed run at exactly 0.0000pt clearance, and bottom-seating moved it clear.

Verified: inputs 45,413 -> **45,467 (+54)**; corpus walk **53/53 green**; blue
census **108, unmoved**; `comb_slots_match_printed` 10/19 and censuses
4,587/33/4,554 unmoved; seven self-tests pass, 19 proven mutations; 43
structure pins re-derived from `build/html`.

**Operator error worth recording.** I first measured this package's audit
immediately after merging, while `build/ir` and `build/layout` still held the
previous package's geometry, and reported 3 forms/6 -- calling the agent's
2 forms/5 an unearned improvement. The gate, which regenerates before
auditing, published 2/5 on a consistent tree. The rule this file already
states ("measure both sides with ONE instrument, on a tree that was actually
written") is the one I broke. Stale `build/` after a merge is not a neutral
starting point.

## T5a — the checkbox squares the lattice could not see (F210 fixed)

**Gate r51: 10/13**, determinism byte-identical (`b47a82b63e68`), open
blocker+major **8 -> 7**. The Taxpayer/Spouse squares in 1701 Part V
Schedule 1 are drawn at gray 0.251, which `tone_role` calls decorative and
`lattice.py:6295` excludes from grid intake, so no lattice line existed at
their edges, the area collapsed to one `label` cell, and `field_verdict`
refused it. The source stated its own intent beside those rules all along:
each square's interior carries a white KNOCKOUT fill -- the sheet painting the
box back to paper.

**Two independent instruments agreed on the population before anything was
built.** An IR-side geometric census (closed box of four decorative rules
around a knockout fill, 4-20pt) and the browser-side tone-aware `vacant`
census from T3+T4 -- entirely separate code paths, one reading the extracted
content stream, the other reading rendered SVG -- both return **22 squares
across exactly 3 forms**: 1700-2018 fourteen, 1701-2018 six, 1701A-2018 two.
All 22 at a single stroke tone. That agreement is what made the target set
trustworthy rather than an artifact of one instrument's assumptions.

The implementing agent measured BOTH candidate mechanisms and chose the
tighter one: knockout-interior-of-a-decorative-box selects exactly those 22
and nothing else, where admitting mid-tone rules into the lattice would have
reclassified cells on 11 and 26 forms that no finding has reviewed.

Verified independently, in a browser, by the user's own method: all six 1701
p2 squares reached by Tab (positions 704/705 for the item-8 Yes/No pair the
user had not spotted, 731/732 and 798/799 for Schedule 1), focused, typed
into, read back. Inputs 45,391 -> **45,413** (+22). Corpus walk 53/53 green.
**Blue census 130 -> 108**, with those three forms at exactly 0 -- the
remaining 108 are a different class (2550M's 59 and 1604CF's 40 wide empty
ruled table rows) and were correctly untouched. Assertions unmoved at 3/6 and
10/19; censuses 4,587 / 33 / 4,554. Seven self-tests pass, 17 proven mutations.

Checked because they touch standing rules: `~/Downloads/forms` was read from
during regeneration and is untouched (274 files, none modified that day,
working tree clean), and no official BIR PDF was committed -- the only PDF in
the diff is the synthetic `fixtures/rules.pdf` that `make_fixtures.py`
generates.

## T5c — a ruled blank is written on its line, not below it (F148/F149 fixed)

**Gate r50: 10/13**, determinism byte-identical (`684cedfdd9f8`), open
blocker+major **10 -> 8**. Verified independently of the implementing agent,
in a real browser, by the method the user used to find the defect: Tab pressed
until `#p4c213-i` took focus (2,413 presses), then typed into and read back.
Corpus walk 53/53 green; 1701 2,835 green / 0 red. Inputs 45,333 -> **45,391**.
Assertions unmoved: `inputs_over_printed_text` 3 forms/6 (the assertion that
killed the r37 attempt), `comb_slots_match_printed` 10/19.

**The standing check is load-bearing, and it proved it immediately.** Right
after the merge, `emit --self-test` FAILED with "53 form(s), 0 rule(s)
claimed" -- `build/` still held artifacts from the pre-provenance extractor, so
there were no underscore rules to check and the `rules_checked > 0` guard
refused to pass while having verified nothing. Regenerating fixed it. A check
that can report success over an empty population is not a check.


**Measured 2026-08-11, worktree `wt/t5c-ruled-blanks`, base `2a34a0c`, on top
of r47.** F148/F149: 1701 p4 item 9 and 1701A p2 item 63 ("Other Tax
Credits/Payments (specify)") had no input for the ruled blank beside the
caption, because the caption and the blank are one `label` cell and
`field_verdict` refuses every `label`. The r37 attempt at this was reverted
(F200) because underscores were TEXT then, so an input on the blank overlapped
a printed run; P3 made a run of underscores draw a RULE instead
(`extract.ruled_blank_bars`), which removed the conflict but left rules with
no way to tell an underscore-drawn bar from an ordinary one.

**Provenance first.** Every rule now carries `origin`:
`RULE_ORIGIN_TEXT_UNDERSCORE` for a bar `ruled_blank_bars` measured off
underscore glyphs, `RULE_ORIGIN_VECTOR` for everything else. `merge_intervals`
takes the origin per contributor and a merged run's own origin is
text-underscore only when EVERY contributor is — a vector fragment abutting a
writing line on the same band ("one stroke on paper") reports vector-origin,
never guessed to be a writing line because part of it is. `Segment.to_ir`
publishes it as a 15th rule key; `SCHEMA_VERSION` does not move (a key added,
per its own comment).

**Then the input.** `emit.field_verdict` gives a `label` cell an input when
its own paper carries a structural, singly-owned underscore-drawn rule
(`RuledBlankWriting`); the box is seated ON the rule (`ruled_blank_field_box`)
— one line tall, bottom edge the rule's own top, x-extent the rule's own — and
a cell carrying more than one blank on its own line ("Page ___ of ___",
"___ % X ___ = ___") gets one region per blank, reusing the same
`region_insets` multi-input mechanism 39 other cells already use for a
printed-divider split.

**Census.** 118 underscore-drawn rules corpus-wide (matches F200's own 118
published). 60 are `role: "structural"`; the other 58 are `role: "knockout"`
(white-on-colour lettering inside a legend/swatch on
1600-PT/1600-VT/1606/1706) and fall inside no lattice cell at all — excluded
by construction, not by a new rule. Of the 60 structural rules, 1 is claimed
by two `label` cells at once — 2550Q p2's fraction bar under "Total Sales",
itself underscore-drawn — and is refused rather than guessed at, the same way
F148's own writing line would be ambiguous between two labels; the other 59
are singly-owned, but two of the 54 owning cells claim more than one rule
(1600wp-2010's "Page ___ of ___" and one row on 1706-2018), so **54 cells /
58 rules / 58 `<input>` elements** across **19 forms**: 1600wp-2010, 1601c-2018,
1603q-2018, 1700-2018, 1701-2018, 1701a-2018, 1701ms-2024, 1701q-2018,
1702mx-2018c, 1706-2018, 1801-2018, 2200a-2020, 2200an-2018, 2200m-2018,
2200p-2020, 2200t-2022, 2550-ds-2025, 2550q-2024, 2551q-2018.

**Nothing else moves.** `inputs_over_printed_text` **3 forms / 6 offenders**,
unmoved (the r43 baseline exactly). `comb_slots_match_printed` **10 forms /
19 offenders**, unmoved. Comb censuses **4,587 subjects / 33 retained / 4,554
comb cells**, unmoved (`comb_referee.py` run against the assertions-only
audit: census fields agree exactly; the deeper position-agreement adjudication
needs a full roundtrip audit, not run here, consistent with not running the
full gate). Tab-walk **53/53 fully green**, `p4c213-i` reached in reading
order between item 8's comb and item 9's own money comb. Inputs **45,333 →
45,391** (+58, exactly the `<input>` count above). Two `batch.py` runs over
the full 53-form corpus are byte-identical (`build/html`, `build/ir`,
`build/layout`, `forms/`). 1701 p4 item 9 verified typeable in a real
Chromium page: `goto` → `click` → keyboard `type` → `input_value()` read back.

**Standing check.** `emit.ruled_blank_corpus_assertions`, run by
`emit.py --self-test`, re-derives the 58-rule claim set against every
`build/ir` + `build/layout` + `build/fonts` triple this checkout has and
fails if any claimed cell lacks a typing surface (`rules_checked > 0` is
asserted too, so a broken discovery mechanism that silently claimed nothing
cannot pass by having verified nothing — proven with two independent
mutations, neither committed). `fixtures/prove_fixtures_fail.py` gained a
dedicated written-here probe (`RULE_ORIGIN_PROBE_STREAM`) and source-level
mutation (`rule-origin`) proving the merge-collapses-to-vector rule can fail;
`extract.py --self-test` gained the matching in-memory check and mutation,
20 checks → 21.

**Pins.** `EXPECTED_HTML_STRUCTURE_SHA256` re-pinned in `comb_referee.py` for
the 19 moved slugs (the other 34 byte-identical); `HTML_RUNTIME_SCRIPT_SHA256`
unmoved (no runtime script text changed).
`AUDIT_DEPENDENCY_SHA256["tools/formgen/extract.py"]` re-pinned to
`extract.py`'s new bytes. `audit.py` and `gate.py` were not touched and their
pins do not move. Review findings: F148 and F149 both `fixed`.

## r49 — the overlay stops inventing walls, and blue becomes actionable

**`?debug=fields` had no tone awareness, so grey decorative tint closed boxes
that are not boxes** — the instrument a reviewer uses to find defects was
generating them (F213). Two tone tests now, because there are two questions:
`isTintTone` asks "can this STROKE bound a box?" and splits at
`RULE_WALL_TINT_SPLIT_GRAY = 0.70`, inside the only genuinely empty interval in
the corpus tone census (0.651 -> 0.7489); `isDecorPaint` asks "is this box's
INTERIOR a shading pad?" over the wider band `(STRUCTURAL_MAX_GRAY, 1)`, 0.15
being the pipeline's own structural cutoff rather than a new number.

The census that settled it, over all 53 IRs: rule tones are eight quantised
values. **0.502 and 0.651 are WALLS** — mid-grey checkbox outlines, 100% and
94% short box edges, with no page-spanning run at 0.251 or 0.502 at all —
while 0.7489/0.8509 are dominated by page-spanning band edges. The plan had
assumed the empty interval sat between 0.251 and 0.7489; measurement refuted
that, and a split placed there would have erased real checkboxes and hidden
F210's whole family.

Measured over 53 forms: `over` (red) fell to zero or dropped on every form and
rose on none; `vacant` (blue) **~2,700 -> 130 across 12 forms** and rose on
none. 0619E 12 -> 0 (its centavo-separator pads are pads, not forgotten
fields). The survivors are real: 1701's four Schedule-1 squares **plus two
item-8 Yes/No squares**, 1700's fourteen — F210's mechanism on a second form,
so it is a family — and 2550M/1604CF's wide empty ruled table rows.

**Two defects found while proving the above, both by testing against the real
corpus rather than trusting the design.** (1) The vacant probe first read
`visibleRects`' FILTERED output, which made every one of F210's squares
vanish: a same-as-paper white knockout that nothing wholly contains is
invisible to a filter that compares against white paper, so the probe found
the tint under it instead of the knockout on top. (2) A structure re-pin was
taken from `forms/` when `EXPECTED_HTML_STRUCTURE_SHA256` locks
`build/html/` — every form then read "bytes changed from the reviewed pin" and
the referee measured 0 of 53. Contention was the first hypothesis (the r48
gate ran at load 65.8 under another project's test suites, and r40 collapsed
that way) and re-running standalone on a quiet machine refuted it: 53
identical errors is not what contention looks like. The pin's comment now
names the tree it locks.

Every tone claim is proven by running the SHIPPED bytes under node: the split
is mutated and required to change a verdict, the paint source is passed both
arguments and required to disagree, and the two tone tests are required to
DISAGREE about 0.651 so a refactor collapsing them trips first.

Also landed: `?debug=tab` renders `tab_check`'s JSON; `window.formgenFieldCensus`
is one census with two consumers, so the blue boxes are burned into the same
`page-<N>.png` as the green/red tab verdicts — one review surface, per the
user's request; `review-serve` re-roots at `forms/` so both trees share a root.

**Gate r49: 10/13**, determinism byte-identical (`2ab6f9da241e`). Referee
healthy: 53 forms measured, 0 errors, 4,587 combs found, **4,508 agree, ZERO
disagreements**, 4,554 active, 33 retained. Assertions unmoved at
`inputs_over_printed_text` 3 forms/6 and `comb_slots_match_printed` 10/19,
verified against a complete 53-form audit after a truncated 48-form run first
read as an improvement. 45,333 inputs. Tab walk 53/53 green. The `findings`
check went from UNEVALUABLE (r47, two malformed entries) to a genuine FAIL
reporting 10/143 blocker+major unresolved — an unevaluable check hides what it
cannot measure, so this is the check working for the first time this round.

## r47 — tab order holds on all 53 forms, and the walk that proves it is a tool

**Keyboard tab order now follows the printed reading order corpus-wide, and
`tab_check.py` measures it end to end.** F209's mechanism: every growable
band's rows were emitted after the page's whole static cell layer, so DOM
order — which IS focus order, no `tabindex` exists anywhere — jumped back up
the page (worst: 697pt on 1701 p4, where the NOLCO continuation rows tabbed
after PART IX). The field layer is now split into `layer-cells` segments
around each band at its own `(y0, x0)`. Measured: reading-order inversions
3,363 across 20 forms → **0 across 53**; inputs 45,333 → 45,333; cell counts,
censuses and both failing assertions unmoved (`inputs_over_printed_text`
3 forms / 6, `comb_slots_match_printed` 10 / 19, decided residual zero);
structural diff confined to the split boundaries on the 22 band forms;
determinism byte-identical. Gate r47 **10/13** (`8e362d61d28f`) — the same
three non-passing checks as r46, for the same reasons; the "audit-complete
claim hides audit failures" forms named by comb-referee are 8 of the 10
comb_slots offender forms, i.e. the known 19-offender residual seen through
the referee's guard, verified against the fresh audit.

**The tab walk is automated with reviewable artifacts.** `tab_check.py` tabs
through every form in headless Chromium, grades each input
green / red-skipped / red-order against the DOM's own `data-row` reading
order, and writes `forms/review/<slug>/tab.json` + per-page PNGs with the
verdicts burned in, plus one `forms/review/index.html` entry point —
phone-viewable, no server, no tabbing. Before the fix it read 20 of the 22
band forms red (the two clean ones genuinely have their band at the page
bottom — verified independently against the shipped geometry); after the fix,
**53/53 fully green in 74.5s**. `just tab-check` / `review-clean` /
`review-serve`; one CI step fails the job on any red. Deliberately NOT a gate
self-test (user decision: browser minutes).

**The walk judges only inputs that exist.** A missing input looks identical
to correct paper from the keyboard. Missing-field detection is T4's
tone-aware blue census plus the ledger — the census this round measured:
rule tones are eight quantised values, the wall/tint boundary sits in the
genuinely empty 0.651 → 0.7489 interval, and 0.502/0.651 are WALLS (mid-grey
checkbox outlines on 11 and 26 forms), which refutes the split this plan
first assumed.

**Ledger:** F207 fixed (ink-band pre-printed test; the sixth
nominal-edge-vs-ink defect this session). F154 and F156 closed
`not-a-defect` on the user's 2026-08-11 visual review — the review refuted
both remaining claims. Filed: F209 (fixed this round), F210 (Schedule-1
checkbox squares drawn at gray 0.251, invisible to the lattice), F211 (0619E/F
signature boxes killed by one caption run; 0620 is the control), F212
(signature typography, minor), F213 (overlay tone-blindness invents walls
from tint — proven on 1701 p2 item 3, `over=4.57pt` against a gray 0.8509
fragment). Open blocker+major 9 → 10, honestly: three closed, four filed.

## r43 — a comb has a HORIZONTAL writing surface, and F199's seven money boxes clear

**`inputs_over_printed_text` goes 6 forms / 13 offenders to 3 / 6, and the seven
that clear are ONE mechanism rather than seven placements.** F208: `comb.slot_x`
runs wall-CENTRE to wall-centre, so every comb's outer compartments were laid
across half of each printed wall's own ink. The vertical twin of this was fixed
at r21 (`lattice.comb_on_writing_surface` insets the band to `writing_y0` /
`writing_y1`); the horizontal analogue had never been written. The physical fact
under all seven offenders was that **the offending glyph's ink stops short of
the wall's inner edge** — 2551M paints the wall left of item 28C at
x 238.92-239.64 and prints the `C` of `28C` to 239.5176, tucked under the rule,
with no blank paper between the label and the wall. The writing surface begins
at 239.64.

The cleared seven, cell for cell: 0605 `p1c3`, 2551M `p1c74`/`p1c79`/`p1c86`,
2553 `p1c79`/`p1c84`/`p1c91`. The surviving six are F199's other two groups and
are untouched by this, correctly: 1604CF `p1c16` and 2316 `p1c38`/`p1c39` are
descenders on frozen date and telephone geometry, and 2316 `p1c62`/`p1c83` and
1701MS `p1c287` are the outline overshoots that were at exactly 0.0000pt on the
advance box.

### `comb_slots_match_printed` 12 forms / 25 → 10 / 19, and the DECIDED residual is now zero

The 6 decided offenders F196 recorded as unfixable are gone — 1701MS `p1c166`/
`p1c173`/`p1c179`/`p1c187`, 2550M `p1c203`, 2550Q `p1c6` — and **nothing new
appeared**: the 19 that remain are the same 19 source-unevaluable subjects as
before, cell for cell, reason for reason. F196's freeze was the same defect seen
from the other side: those six rails' own CENTRES fall 0.26-0.31pt outside their
owner's fused edge, so "outer `slot_x` within 0.25pt of the rail" and "every
emitted slot inside the cell rectangle" could not both hold. Measured against
the rail's INK EDGE instead of its centre, both hold with room: 1701MS `p1c166`
publishes 370.66 against a right rail whose ink starts at 370.68.

### The contract, and its five consumers

`lattice.comb_rails` now reports each outer rail's own painted ink beside its
position (`left_rail_ink` / `right_rail_ink`), measured from exactly the bars
that established that position and from no others.
`lattice.comb_on_writing_surface` insets `slot_x`'s outer values to those ink
edges and publishes `writing_x0` / `writing_x1` / `writing_width_pt`, with
`writing_x_rails` naming what derived each side. **`slot_x`, `divider_x`, the
compartment counts and the pitch are untouched**, exactly as the vertical twin
leaves `y0`/`y1` alone: two questions, two sets of keys.

| Consumer | Reads |
| --- | --- |
| `emit.comb_slot_edges` | the one reader; feeds the slot div, the input inside it, and the band-template JSON a cloned row is re-laid out from |
| `audit.emitted_comb_evidence` | `emission_layout_outer_position` against `writing_x0`/`writing_x1`; `emission_source_outer_position` and `layout_source_outer_position` against `source_frame_geometry`'s `left_rail.ink_x1` / `right_rail.ink_x0` |
| `comb_referee.emitted_geometry_contract` | one of two independent re-derivations of the emitted slot rectangles |
| `gate._emission_geometry_from_layout` | the other, kept independent and moved with it |
| `comb_referee.audit_offender_dimensions` | re-derives the audit's own rail expectation, so it names the ink edge too |

**No tolerance moved.** `POSITION_TOL_PT` is still 0.25 and
`EMITTED_GEOMETRY_EPS_PT` still 0.0002; only the comparison TARGET changed, from
a rail's centre to that rail's inner ink edge, which is comparing an inner edge
against an inner edge. Reusing `slot_x` for both jobs cannot work: the half-wall
inset is 0.36-0.48pt and 38.7% of the corpus's 8,306 flush outer edges exceed
0.25pt against the centre.

A second change lands with it: `audit.input_boxes` now reads a comb input's OWN
declared inset instead of scoring its slot div. Every comb input in this corpus
declares `inset:0`, so this moves no number today; without it any producer-side
move inside a slot — in either direction — is invisible to the judge. Where the
attribution is ambiguous it keeps the larger slot rectangle, which can only
report more overlap and never less.

### Where the inset could not be measured: 8 combs, fail closed and counted

`writing_x_rails` publishes `rail-ink` on both sides for **4,546 of 4,554**
combs. Eight fail closed to `slot_x`'s own value on one or both sides, because
no bar of that rail crosses the comb's band, and every one is a form whose walls
this branch already had to heal: 1700 `p1c21`/`p1c68`, 1707 `p2c38`, 2000-DST
`p1c4` (left) / `p1c5` (right), 2200C `p1c7`/`p1c11` (one side each) and 2200C
`p1c13` (neither side). **Zero** combs surrender to a degenerate width. Nothing
is guessed: a fused lattice edge times a nominal thickness is not a measurement
of the rail at that band, and the code refuses to treat it as one.

### Nothing becomes unusable, and this is NOT the reverted 2550M trade

Insetting every comb in the corpus leaves a narrowest outer compartment of
**6.12pt** (1604CF `p1c30`), and nothing under 6pt. The insets themselves run
0.01 / 0.24 / 0.96pt (min / median / max). The r22 trade that had to be reverted
cost a fitted face by cutting the writing box to a 3.12pt stub; this one cannot,
because a comb's size is fitted to its writing HEIGHT and capped at the sheet's
body size, never to its slot width — which is why the `<input>` attribute
multiset is byte-identical in all 53 documents.

### Corpus census — r43

| Quantity | r43 | prior | Note |
| --- | --- | --- | --- |
| Bundles / unique codes / pages | 53 / 50 / 116 | same | unchanged |
| Comb ledger subjects (`EXPECTED_COMBS`) | **4,587** | 4,587 | **did not move**: `slot_x` and `divider_x` are untouched, so no ledger topology digest moves |
| Retained subjects (`EXPECTED_RETAINED_SUBJECTS`) | **33** | 33 | unchanged, slug for slug |
| Cells carrying a comb | **4,554** | 4,554 | unchanged |
| Combs with a measured rail-ink inset | **4,546 of 4,554** | — | 8 fail closed, 0 surrendered |
| `<input>` corpus-wide | **45,494** | 45,494 | unchanged |
| Emitted documents changed | **53 of 53** | — | almost every comb has an outer compartment |
| Tag inventory across the 53 | **unchanged in every document** | — | nothing added, nothing deleted; visible text token-for-token identical in all 53; `<input>` attribute multiset identical in all 53 |
| Slot rectangles moved | **9,307 of 40,185** | — | every one a first or last compartment; no document's slot COUNT moves; every internal divider byte-identical |
| Embedded `<script>` | **runtime byte-identical in all 53** | — | only the 15 `formgen-bands` JSON blobs move, so `HTML_RUNTIME_SCRIPT_SHA256` does NOT move |
| `EXPECTED_HTML_STRUCTURE_SHA256` | **53 of 53 re-pinned** | — | the review is recorded at the pin |
| `LATTICE_PRODUCER_SHA256` / `AUDIT_PRODUCER_SHA256` | **both re-pinned** | — | with their causes named at each pin |
| Determinism | **two generations byte-identical** | — | 461 files, tree digest `b0b55926b1fdab39` |

### Assertions — corpus-wide `audit.py --assertions-only` at r43

| Assertion | r43 | prior |
| --- | --- | --- |
| `inputs_over_printed_text` | **3 / 6** | 6 / 13 |
| `comb_slots_match_printed` | **10 / 19** (19 unevaluable, 0 decided) | 12 / 25 (19 + 6) |
| `inputs_span_no_printed_divider` | **HOLDS, 0** | 0 |
| `money_boxes_have_inputs` | **0** | 0 |
| every other assertion | **0** | 0 |

### Reported loudly: one consumer outside this change's five files is now stale

`fill_check.py` measures each typed glyph's centre against the layout's own
`slot_x` centre. The two OUTER compartments are no longer laid on `slot_x`, so
its expected centre for them is half an inset away from the box the character
is actually typed in: **2,046 of the corpus's 9,108 outer compartment centres
shift by more than `DEFAULT_CENTRE_TOL_PT` (0.25pt)**, and a `fill_check.py` run
would report those as over-tolerance slot landings that are not. Nothing else in
it is affected — the glyph window and the slot assignment both widen rather than
narrow, so no glyph is lost or misassigned. The fix is the same three lines
`emit.comb_slot_edges` already is, applied at `fill_check.py:341`; it was not
made here because that file was outside this increment's declared ownership.
`fill_check.py` is not one of the gate's twelve checks.

## r27 — a caption block is not a comb, and an occupied compartment is not a box

**`inputs_over_printed_text` goes 20 forms / 147 offenders to 12 / 33, and it is
NOT the r22 trap.** r22 lowered this same number by cutting every comb's typing
surface to a 3.12pt stub, which hid the defect and made the fields unusable.
This time nothing shrank, and that is measured rather than asserted: **all 7,405
slot rectangles that survive in the ten changed documents were compared
attribute-string to attribute-string against their r26 selves and ZERO moved.**
The only rectangles that disappear are the 22 belonging to the eleven refuted
caption blocks. Per-document minimum slot height is unchanged in all ten
(16.32 / 14.88 / 14.52 / 12.96 / 12.96 / 14.52 / 12.96 / 12.96 / 12.84 /
15.09pt); only the MAXIMUM falls, from 103.83pt — 1606's whole rate table as one
"compartment" — to a normal box.

### Reported loudly: `comb_slots_match_printed` got worse, and this fix did it

22 forms / 193 offenders → **23 / 203**. The mechanism is exact and is finding
**F192**, not a mystery: refusing an occupied compartment in the INTERIOR of a
comb makes `comb_slots_match_printed` publish `invalid-emission` — "one or more
comb inputs do not identify their owning slot" — because it pairs the k-th
input with the k-th compartment while `data-slot-index` already carries the
compartment's true number. **94 cells gained that failure kind, which is
exactly the number of compartments the emitter now refuses; the correspondence
is one-to-one.** 76 of the 94 were already offenders under
`source-topology-unevaluable` and move no count. The 18 that were not are
2200S's 16 money combs, 1800 `p1c68` and 2550-DS `p1c79`, and 2550-DS crossing
`holds: true → false` on that one cell is the entire +1 form.

This is **G16**, already on the board as G11's unpaid half — "the assertion that
owns the emission contract was not told the contract changed". The assertion is
not weakened here and must not be. Against it: 89 of the 147 offenders it cost
to leave in place were taxpayer typing surfaces laid on printed ink.

Two other movements inside that assertion, both downward and neither r27's:

- 2200A 27→26, 2200C 25→24, 2200P 26→25 — the Bureau band ceasing to claim to
  be a comb, which is **F187 closed on evidence**.
- 2550M 8→3 — **r24's G19/F184 fix, first measured here.** The newest audit
  artifact in the tree was r23's (`build/audit-r23b.json`), and 2550M's emitted
  document is byte-identical across r27's regeneration while that audit
  describes a 3-slot `p1c89` the tree no longer contains. The −5 predates this
  round and is attributed to r24, not claimed for r27.

### The referee: 46/53 → 50/53 after a census pin moved, and 3 forms I did not fix

Two separate things, and only one of them was mine to touch.

**The census half, moved with its cause.** `EXPECTED_RETAINED_SUBJECTS_BY_SLUG`
goes **22 → 33** on exactly seven slugs (1606 +1, 2200A +2, 2200AN +1, 2200C +1,
2200P +2, 2200S +1, 2200T +2), and every one of the eleven new subjects carries
the single reason code `emission-suppressed-caption-block-not-character-cells`.
That pin's own comment demands this: "retention appearing on a new form is a
census move that must be declared here". `EXPECTED_COMBS` stays **4,583** and
**no per-slug comb count moves**, because active + retained is what that
denominator counts — a refuted comb does not leave the ledger.

**The contract half, deliberately left failing — F191.** The referee requires a
retained subject's `legacy_comb` to be `unresolved`, encoding "retained because
we could not resolve it". A refuted caption block is a third shape: resolved
geometry, refuted semantics. 5 of the 11 refuted subjects happen to carry
`unresolved` and are accepted; **6 on 2200A / 2200C / 2200P carry `resolved` and
the referee refuses the whole form.** Widening the adjudicator in the same
increment as the producer it adjudicates is the failure already paid for at
`EXPECTED_COMBS` and `HTML_RUNTIME_SCRIPT_SHA256`, and a producer rewriting its
own published `resolution_status` to satisfy its referee would be the same fault
in mirror image. The referee's refusal is fail-closed and correct on its own
terms.

### The cross-file contract the lattice declared and nobody implemented

`lattice.REFUTED_CAPTION_BLOCK_REASON_CODE` is published on the retained
subject, and `audit.validate_comb_owner_registry` admits a retained subject only
on a reason-code tuple it knows. Measured before the fix: 1606, 2200A and 2200S
all returned `retained_unresolved suppression reason evidence is malformed`,
which invalidates the **whole form's** owner registry, not the one record.
audit.py now names the tuple and routes it through exactly the identity branch
the no-band tuple goes through — suppressed, blocking, comb-less, and an
identity mapping onto its own still-present cell, both directions asserted in
the self-test. Nothing is weakened; a shape that exists is named.

### The four user-visible checks, looked at rather than counted (r27)

Screenshots at 3× device scale over the shipped `forms/` tree through a local
static server, in the session scratchpad under `r27/`.

| Check | Verdict | Evidence |
| --- | --- | --- |
| 1606 p2's statutory rate table is NOT typeable | **YES** | `1606-p2-rate-table-not-typeable.png`. `p2c135` is `class="c"` `data-cell-kind="label"`, **0 slots, 0 inputs**; clicking its centre leaves `document.activeElement` at `BODY` and typing `999` produces no value anywhere. Schedule 4's Exempt / 1.5% / 3.0% / 5.0% / 6.0% render as printed text |
| 2000-DST's money grid IS still typeable, digits included | **YES** | `2000-dst-money-grid-typeable.png`. `p1c102`: 14 compartments, **13 live**, slot 11 the printed bullet and inert; digits typed into all 11 peso boxes and both centavos boxes. 30 money combs on the page, none left without a typing surface |
| 2550M item 4's TIN slots are still ~14.16pt | **YES — 14.16pt, unchanged** | `2550m-item4-tin-writing-box.png`. `p1c9`/`p1c11`/`p1c13`, browser-measured slot height **14.16pt in a 15.60pt row**, 3 inputs each. The document is byte-identical to r26 |
| 2316 item 3's TIN still shows 14 boxes | **YES — 14, unchanged** | `2316-item3-tin-14-boxes.png`. `p1c17`+`p1c12`+`p1c14`+`p1c16` = 3+3+3+5 = **14 compartments, 14 inputs**, a `9` typed into every one. The document is byte-identical to r26 |

One extra, because it is the refutation's other face:
`2200s-masthead-not-typeable.png` — the sheet's own "BIR Form No. 2200-S /
EXCISE TAX RETURN for Sweetened Beverages" title, 0 inputs where it used to
carry two.

### Corpus census — r27

| Quantity | r27 | prior | Note |
| --- | --- | --- | --- |
| Bundles / unique codes / pages | 53 / 50 / 116 | same | unchanged |
| Comb ledger subjects (`EXPECTED_COMBS`) | **4,583** | 4,583 | **did not move, and must not have**: a refuted comb stays in the ledger |
| Retained subjects (`EXPECTED_RETAINED_SUBJECTS`) | **33** | 22 | +11, all `caption-block-not-character-cells`, on 7 slugs |
| Cells still carrying a comb | **4,550** | 4,561 | the 11 refutations |
| Emitted documents changed | **10 of 53** | — | 43 byte-identical, including 2550M and 2316 |
| Tag inventory across the ten | **−110 `<input>`, −22 `<div>`** | — | nothing added; visible text token-for-token identical in all ten; every embedded `<script>` byte-identical, so `HTML_RUNTIME_SCRIPT_SHA256` does not move |
| `EXPECTED_HTML_STRUCTURE_SHA256` | **10 of 53 re-pinned** | — | the other 43 untouched |
| Findings | **192**, **33 blocker+major open of 132** | 32 of 129 | F187 closed; F188/F189 filed already fixed; F190 filed open (a live input over "27 Tax Debit Memo" on 3 forms); F191/F192 filed open as minor |

The 110 inputs are three named populations: **92** money decimal bullets, each
ONE compartment of a 14-, 29- or 33-compartment comb with the two centavos boxes
to its right; **16** on the 8 refuted caption blocks that had inputs; and **2**
completing the printed rate `0 %` on 1800 `p1c68` and 2550-DS `p1c79`.

## Gate — full clean-tree run r27 (2026-08-08, `522cb44`) — 9 of 12 PASS

    PASS  self-tests 10 · conversion 53/53 · rules 53/53 · paper 53/53
    PASS  artwork 53/53 · text 53/53 · tracked-files · audit-refresh 53
    PASS  determinism 91712db2b4b4  (moved, and had to: ten form documents
                                     changed. Two generations still compare
                                     byte-for-byte)
    FAIL  assertions    inputs_over_printed_text        12/53  (r25: 20)
                        comb_slots_match_printed        23/53  (r25: 22)
                        inputs_span_no_printed_divider   5/53  (r25: 5, and
                                                                offender-for-
                                                                offender the
                                                                same 33)
    FAIL  findings      33/132 blocker+major open (r25: 32/129)
    UNEV  comb-referee  46/53 forms at 522cb44

Same three checks red as r25. **The referee line is 46/53 as the gate scored it
and 50/53 after the census pin moved**, measured by a standalone referee run at
payload `a3a97860` rather than by a second full gate; the corpus is
byte-identical across that pin move, so the determinism digest and the other
eleven checks are unaffected by it. What is left is F191's three forms and the
runtime attestation, which is deliberately non-enforceable and is not this
round's work.

## r23 — the three regressed assertion families, and what it cost to fix two of them

**r23 starts nothing. It is r21/r22's three regressions, paid.** Two of the
three are gone; the third is gone as a *form count* and its residue is filed.
One family got worse and that is reported first, not last.

| Assertion | r20 | r22 | **r23** | |
| --- | --- | --- | --- | --- |
| `comb_slots_match_printed` | 22 / 188 | 36 / 254 | **22 / 193** | form count back to r20 |
| `money_boxes_have_inputs` | 0 / 0 | 4 / 4 | **0 / 0** | **PASSES again** |
| `printed_box_peers_all_fillable` | 0 / 0 | 1 / 1 | **0 / 0** | **PASSES again** |
| `inputs_span_no_printed_divider` | 11 / 67 | 5 / 33 | **5 / 33** | unmoved, offender-for-offender |
| `inputs_over_printed_text` | 20 / 149 | 19 / 131 | **20 / 147** | **WORSE by 1 form / 16 offenders** |

### Reported loudly: `inputs_over_printed_text` got worse, and the fix did it

**+21 new offenders, −5 cleared, 19 forms → 20.** Every one of the 21 is the
population STATUS.md has carried since the writing-surface increment: a comb
cell whose lattice rectangle spans **caption and comb**, so a writing box that
fills the cell reaches the caption printed in its upper half. The offenders
name themselves — `"   Zip Code"` (1600WP `p1c24`), `"Telephone No."` and
`"Zip Code"` (1604CF `p1c16`/`p1c21`), 2551M `p1c74`/`79`/`86`, 2553
`p1c79`/`84`/`91`, 2316 `p1c37`–`40`, 2550M `p1c89`–`91` — the same cells, cell
for cell, that the earlier increment listed under this exact heading.

**r22 had not fixed them. r22 had hidden them**, by shrinking every comb's
typing surface to the 3.12pt divider band, which is too short to reach any
caption and too short to type in. A number that improved because the field
stopped being usable is not an improvement, and restoring the field restores
the debt. The fix belongs in `lattice.py`'s cell segmentation (row **G05**),
not in this assertion and not in the emitter.

### What fixed the other two: emit.py lays out on the WRITING box (F186)

`emit.comb_writing_rect` is now the **one** reader of a comb's vertical extent
for everything the emitter draws — the slot div, the input inside it, the
band-template JSON a cloned row is re-laid out from, and the face `field_box`
fits — and it returns `writing_y0`/`writing_height_pt`. The **divider band**
(`y0`/`y1`/`height_pt`) survives emission unmodified, because that is the
contract `comb_referee.classify_band` seeds source topology from and the one
the reviewed 2551Q control was signed against. `emit.py`'s new
`comb_writing_rectangle_assertions` drives both halves at once, and a mutation
that restates the writing box into `y0`/`y1` fails it.

In the shipped bytes: 2550M `p1c9` (item-4 TIN, a 15.60pt row) reads
`top:0.72pt;height:14.16pt` on all three slots where r22 shipped **3.12pt**;
2316 slot 0 reads `top:0.45pt;height:13.92pt` against r22's
`top:8.71pt;height:6.05pt`.

**Why that moved an assertion nobody edited.** `audit.py`'s
`comb_slots_match_printed` asks the SOURCE whether it printed a constant in
each **emitted** slot rectangle — that is how G16 was closed, and it is right.
With the rectangle collapsed onto a 3pt band, the query landed where the
constant is not, so 64 correctly-refused compartments across 25 forms read as
`editable comb slot has no live input element and the source prints no
constant or shading in that compartment`. Restoring the rectangle takes that
population **64 → 3**. The assertion was not touched.

### The one exclusion added, and its blast radius is ONE box

0605's `p1c17` — `BCS No./Item No. (To be filled up by the BIR)` — is blocker
**F147**, fixed at r22: the box is emitted with no input and
`data-preprinted="bureau"`. Two assertions then reported it, and both were
right on their old model of the paper and wrong about this box:
`money_boxes_have_inputs` called it an `enclosed empty box, no input`, and
`printed_box_peers_all_fillable` reported it against four fillable row peers.

`audit.source_bureau_reservations` now reads the reservation **from the pinned
PDF's own text operators** (`drawn_glyph_boxes`) and from nothing else — not
from `emit.BureauReservation`, not from the IR, not from the layout. The two
answer the same question about the paper through different producers, which is
what still lets this one catch an emitter that reserves a box the sheet does
not.

It is not a relaxation and the numbers say so:

- **Corpus-wide it claims exactly ONE box**, 0605 `p1c17`, and every caller
  publishes the count as `boxes_bureau_reserved`, declared in
  `gate.BASIC_ASSERTION_COUNT_FIELDS` so an undeclared count would fail the
  gate rather than pass quietly.
- The rectangle reported is the **matching phrase's own glyphs, never its
  line**: 0605 sets `Return Period (MM/DD/YYYY)` and `BCS No./Item No. (To be
  filled up by the BIR)` on ONE baseline, and a line-wide rectangle would hand
  the taxpayer's Return Period boxes the Bureau's excuse. A mutation to the
  line-wide rectangle fails two new `audit.py` self-test assertions.
- The phrases are matched **without spaces**, because `drawn_glyph_boxes` drops
  whitespace glyphs; the list quotes the paper including 0605's own missing
  "by", and `(To be filled up by the taxpayer)` does not match.
- Prose is refused: `The machine validation shall reflect the date of payment`
  reserves nothing, while `Machine Validation/Revenue Official Receipt Details`
  does — line-start, not substring.

2200-A/C/P's Bureau band needed **no exclusion at all**: with the writing
rectangle restored its compartments are no longer ink-free, and
`money_boxes_have_inputs` cleared them on the source's own answer.

### The residue, filed rather than absorbed

Three offenders remain inside `comb_slots_match_printed` — 2200A `p1c115`,
2200C `p1c105`, 2200P `p1c114`, the Bureau band's left compartment — because
that assertion asks the source for **ink** and a reservation is a caption. It
is **F187** (minor). None of the three changes its form's verdict: those forms
fail on 27, 25 and 26 offenders of their own. It is deliberately not fixed
here, because that assertion's published shape is contract-bound by
`comb_referee._normalise_outer_comb_assertion`, and moving the referee's
subject and the referee in one increment is what GOAL.md's user decision 1
forbids.

### The four user-visible checks, looked at rather than counted (r23)

Screenshots at 3× device scale over the **shipped `forms/` tree** through a
local static server, in the session scratchpad under `r23/`.

| Check | Verdict | Evidence |
| --- | --- | --- |
| 2550M's comb slot height is the writing box, not 3.12pt | **YES** | `F186-2550m-item4-tin-writing-box.png`. Browser-measured slot 18.88 CSS px = **14.16pt** in a 15.60pt row; the typed digits sit centred in the printed box, not on its floor |
| 2316 item 3 TIN still shows 14 boxes | **YES — 14, unchanged** | `F111-2316-item3-tin-row.png`. `p1c17`+`p1c12`+`p1c14`+`p1c16` = 3+3+3+5 = **14 compartments, 14 inputs**, a `9` typed into every one. F111's fix at r22 is intact and r23 did not disturb it |
| 0605 BCS No./Item No. is NOT fillable | **YES — still refused** | `F147-0605-bcs-bir-only.png`. `p1c17` carries `data-preprinted="bureau"` and **0 inputs**; the caption "(To be filled up by the BIR)" is printed above an empty box with nothing to type into |
| A real money box on each money_boxes-failing form IS fillable | **YES, all four** | `money-2200a.png`, `money-2200c.png`, `money-2200p.png` — the `Tax Payment/Deposit` comb, **14 compartments each, every one typed into**, decimal separator intact. `money-0605.png` — 0605 item 21 `Total Amount Payable` holds `1,234,567.89`. 0605's money boxes are plain fields, not combs, so the currency shot there is a text field by construction |

### Corpus census — r23

Nothing but geometry moved. **No comb census pin moved and none should have:**
`lattice.py` is byte-identical to r22, so the ledger is the same ledger.

| Quantity | r23 | r22 | Note |
| --- | --- | --- | --- |
| Bundles / unique codes | 53 / 50 | 53 / 50 | unchanged |
| Pages | 116 | 116 | |
| Comb ledger subjects (`EXPECTED_COMBS`) | **4,583** | 4,583 | unchanged — no lattice change |
| Active comb cells / retained | **4,561 / 22** | 4,561 / 22 | re-derived from the fresh `build/layout` |
| Emitted inputs | **45,765** | 45,765 | unchanged |
| Comb slot divs | **40,213** | 40,213 | unchanged |
| Comb slots with no input | **287** | 287 | the compartments the source already filled in, plus the Bureau's |
| Form documents changed | **53 of 53** | — | plus `forms/index.html`; every one an attribute-value change |
| Findings | **187** | 186 | **32 blocker+major open of 129** (r22: 33) — F186 closed on the shipped bytes, F187 filed open (minor) |

**Pins moved, each with its cause recorded at the constant:** all 53
`EXPECTED_HTML_STRUCTURE_SHA256` (the emitted documents' geometry) and
`AUDIT_PRODUCER_SHA256` `8d22a957` → `cf7ed2bd` (the Bureau reservation).
`HTML_RUNTIME_SCRIPT_SHA256` was re-derived and did **not** move — all three
pinned runtime scripts are byte-identical, which is the standing evidence that
a layout change did not reach the page runtime.
`LATTICE_/EXTRACT_/VERIFY_PRODUCER_SHA256` are unchanged and still match.

**The 53 re-pinned documents were reviewed, not rubber-stamped**, and the
review is unusually strong: the tag inventory delta is **ZERO for every tag
name** — 239,562 elements before and 239,562 after, nothing added and nothing
deleted — and **visible text is token-for-token identical in every one of the
53**. The whole change is `style` attribute values on `<div class="s">`.

All 11 self-tests pass (10 modules plus `validate_tree`), including a new
`audit.py` mutation-proven pair for the reservation rectangle and a new
`emit.py` block that drives the writing-box/divider-band split in both
directions.

## Gate — full clean-tree run r23 (2026-08-08 00:49, `912c6ed`) — 9 of 12 PASS

    PASS  self-tests 10 · conversion 53/53 · rules 53/53 · paper 53/53
    PASS  artwork 53/53 · text 53/53 · tracked-files · audit-refresh 53
    PASS  determinism  byte-identical (ba1bd2d8c47e). The digest MOVED and had
                       to: all 53 form documents changed. Two generations still
                       compare byte-for-byte
    FAIL  assertions   inputs_over_printed_text        20/53  (r22: 19)
                       comb_slots_match_printed        22/53  (r22: 36)
                       inputs_span_no_printed_divider   5/53  (r22: 5)
                       money_boxes_have_inputs          GONE  (r22: 4/53)
                       printed_box_peers_all_fillable   GONE  (r22: 1/53)
    FAIL  findings     32/129 blocker+major open  (r22: 33/129)
    UNEV  comb-referee 2550M p1c89/p1c90 — IDENTICAL to r22, see below

**Same 9 of 12 and the same three red checks as r22, with TWO assertions fewer
inside the red one.** `money_boxes_have_inputs` and
`printed_box_peers_all_fillable` no longer appear in the `assertions` detail at
all: that is the full clean-tree gate confirming the 4 → 0 and 1 → 0
measurements. `comb_slots_match_printed` is back to r22's-predecessor 22 forms.

### The comb referee is UNEVALUABLE for exactly r22's reason, and r23 did not touch it

Reported loudly rather than netted off. The gate's verdict is character-for-
character what r22 produced:

    measured source certificate schema is unsupported: 2550m-2007/p1c89
    measured source certificate schema is unsupported: 2550m-2007/p1c90
    form total disagrees with evidence: 2550m-2007/referee_layout_mismatches
    form total disagrees with evidence: 2550m-2007/referee_layout_position_mismatches
    report total disagrees with forms: referee_layout_mismatches
    report total disagrees with forms: referee_layout_position_mismatches

`p1c89` and `p1c90` are **F184's cells** — the 2550M Schedule money boxes where
the source strokes two ticks and then paints a white fill over one of them, so
the layout carries a compartment the paper does not print. r23 changed nothing
on that path: `lattice.py` is byte-identical and the referee's own derivation
was not edited. **This is the one thing standing between 9/12 and 10/12, and it
is r22's debt, not r23's.** Closing it means the reviewed `retired_proven_false`
transition F184 already names, which needs independent evidence and a human —
not an integration-time edit to the adjudicator.

## r20 — a printed box a taxpayer must tick now has somewhere to tick

**`printed_box_peers_all_fillable` goes 14 offenders on 14 of 53 forms to ZERO
on zero, and `audit.py` is byte-identical while it happens** (sha256
`8d22a957…`, the r18 pin, unmoved). The assertion was not read, referenced,
narrowed or re-pinned by either producer fix. It is the first of the four red
assertion rows to go green since it was written.

The other three did not go green, and one moved the wrong way. All four, r19 →
r20, forms of 53 and offenders:

| Assertion | r19 | r20 | |
| --- | --- | --- | --- |
| `printed_box_peers_all_fillable` | 14 / 14 | **0 / 0** | **PASSES** |
| `inputs_span_no_printed_divider` | 11 / 79 | 11 / **67** | 24 offenders cleared, 11 appeared; form count unmoved |
| `inputs_over_printed_text` | 20 / 149 | 20 / 149 | unmoved, offender-for-offender |
| `comb_slots_match_printed` | 22 / 185 | 22 / **188** | **+3, reported loudly below** |

### The two producer bugs behind the checkbox class (`lattice.py`)

**1 — a lattice line did not count its own defining rule as coverage.**
`cluster_collinear` chains rules by pairwise *adjacency*, so a cluster can be
wider than `CLUSTER_TOL_PT` (0.3) and its position is the *mean* of its
members' centres. `GroupGeometry.span` then filtered `all_ink` by distance to
that mean and could therefore drop a rule that is itself a member. On 0619-E
the "Amended Return? Yes" checkbox's left wall (centre 275.64) is one of ten
fragments in the cluster at 275.99 — 0.35 > 0.30 — so the column claimed no ink
over the box's own 12pt and the box merged leftward into the caption.
`line_thickness_gray` already exempts a cluster's own rules for weight and
tone and says so in its docstring; `span` now does the same.

**2 — a text run's WHITESPACE was counted as printed text inside a printed
box.** `assign_points` placed a run by its bounding-box centre, and a bounding
box is the run's *advance*, not its ink. `Calendar        Fiscal ` spans
66.5–148.92; its centre 107.7 lands inside the checkbox drawn at 106.08–119.52
**in the gap between the two words**, so the box held "text", was not
`is_empty`, and `classify_cell` returned `label`. The other eight are the same
sentence: `Yes      No` (1706, 2200M), ` 2nd      3rd` (2553), `?        `
(2200S), `        23B` (2551M). `glyph_ink_spans` now reads the per-character
origins and advances every run in the corpus already carries and returns the
extents of the NON-BLANK characters; a run whose home cell holds any of its ink
does not move, so the 1,575 runs whose centre merely falls between two letters
are untouched.

### The comb class (`extract.py`): PDF 32000-1 §8.4.3.3 was not modelled

A round (`J 1`) or projecting (`J 2`) cap inks **half a stroke width past the
declared endpoint** of an open subpath. The IR published those strokes at their
declared endpoints, so a comb tick stopped 0.36pt short of the rail it lands on
and `lattice.split_verticals` filed it as a box border — the compartment
disappeared. 340 of this corpus's 569 open strokes carry such a cap.

`cap_extension_pt` and `open_stroke_ends` model it, applied to the two ends of
a **reconstructed subpath** only: never to `re`/`qu`, never to a polyline that
returns to its own start, never to an interior join — capping per op would have
grown 133 rectangles-drawn-as-four-`l`-ops by half a stroke on all four sides.
No fixture in either corpus draws a round cap, so a written-here probe page
(`CAP_PROBE_STREAM`, 200×200, 13 asserted cases) proves both directions, with a
mutation that restores exactly the old behaviour.

**What it bought, on the paper:** 2550M item 1 `For the Month of (MM/YYYY)` —
the user's original "four year boxes rendered as one big box" — is now four
compartments with one input each, screenshotted with `2 0 2 7` typed into them
(`scratchpad/blockers/F180-2550m-item1-year-comb.png`). All eight of F180's
named inputs left the offender list.

### Reported loudly: `comb_slots_match_printed` got worse by 3

Not hidden and not explained away. The move is four separate things:

    -1  emission-source-position-mismatch          (2550M, genuinely fixed)
    -1  emission/layout-source-outer-position      (2550M, genuinely fixed)
    -1  source-topology-unevaluable                (181 -> 180)
    +5  layout-printed-mismatch + emission-printed-mismatch   (2550M, NEW)

The +5 is one mechanism and it is now finding **F184**. 2550M's Schedule money
boxes get one more compartment than the sheet prints, because a slot boundary
is taken from a divider the page's own `comb_divider_final_visible_ids`
excludes: the source strokes two ticks in the MM box (x 260.40 and x 263.52),
then paints a **white fill over the whole box** (seqno 477) after the 263.52
tick (seqno 419) and before the other, so only one tick survives to the paper.
A 30× raster of the pinned PDF shows one tick
(`scratchpad/blockers/2550m-p1c89-ticks.png`). The layout already records the
right answer beside the wrong one — `final_visible_candidate_cells: 2`,
`reason_codes: [final-visible-count-regression, legacy-continuity-only]` — so
the subject is `active_unresolved` and already blocks the gate. **Deliberately
not patched here:** dropping a legacy comb topology is the reviewed
`retired_proven_false` transition, which needs independent evidence and a human,
not an integration-time edit to another agent's file.

### A registry invalidation found on the way, and fixed (`lattice.py`)

The first r20 regeneration made `comb_slots_match_printed` worse by **13**, not
3. Cause: a suppressed subject's `mapped_partition_cell_ids` is a *partition*,
and nothing enforced it. Once 2550M's `p1c7` (66.00, 118.80, 99.84, 134.40)
lost its rectangular owner too, it and the row band `p1c6` (28.80, 117.12,
582.72, 136.32) that contains it both claimed `p1c116`, `p1c122`, `p1c123` —
and `audit.validate_comb_owner_registry` correctly invalidated the **whole
form**, taking all 17 of its comb subjects to `source-topology-unevaluable`.
`resolve_retained_partition_overlaps` gives a contested cell to the smallest
claiming area. Corpus-wide: 3 cells contested, on one page of one form, no
mapping emptied, and the registry-invalid offender count is back to 0.

Attributed by bisection over the two producers, both directions:
`new extract + old lattice` reproduces it exactly; `old extract + new lattice`
does not. So the trigger is the cap model and the defect is the ledger's.

## PT 060 reads 2%. It is officially 2%. FIXED at r19.

**This is the third time this claim has been made and the first time it is
measured on the tree that was written.** r17 closed it wrongly, r18 retracted
that closure and reported `fixed: false`, and r19 lands it.

`forms/2551m-2002/guide.html`, first `gl-table`, the PT 060 row, verbatim from
the shipped bytes:

```html
<tr><td>PT 060</td><td>Franchises on electric utilities, gas and water utility</td><td>2%</td><td></td><td>performing quasi-banking functions</td><td>5%</td></tr>
```

The table is 19 x 6 where it was 19 x 4. PT 060 carries **2%** in its own Tax
Rate column, and the `5%` that used to sit against it is back in the RIGHT
half's rate column where the source printed it — it belongs to PT 111.

**Scored by a checker that shares no producer with `emit.py` or `guides.py`**
(`scratchpad/r19_rate_check.py`, written for this closure; it reads the shipped
HTML and compares against the 15 rates read independently out of
`~/Downloads/forms/2551M/2551m.pdf` sha256 `f678be68…` page 2 with
`pdftotext -layout`):

| | ATC codes carrying exactly their official rate |
| --- | --- |
| shipped bytes at r18 (`HEAD:forms/2551m-2002/guide.html`) | **0 of 15** — the table had four columns, so no code→rate association existed at all |
| shipped bytes at r19 | **15 of 15** |

Token census across the same two files: **1,283 tokens before, 1,283 after; 20
percent-tokens before, 20 after.** Nothing was added and nothing was dropped —
only re-associated.

### The owner named in G13, STATUS.md and F167 was wrong, and that is why it failed twice

All three named **`guides.py`'s reflow**. `BLOCKER-PLAN.md` C9 named `emit.py`
and was right. The defect is `emit.py:reflow_page` → `_column_bands` →
`_table_markup`. So r18's proof that "`guides.py` is byte-identical" proved
nothing about this defect, and the misattribution is the reason the fix did not
land twice.

**Mechanism, measured.** 2551M page 2's ATC schedule has exactly one horizontal
rule in its whole 170pt band — the table foot — so `lattice.py` can offer only a
single 568 x 185pt `label` cell and the ruled-grid path has nothing to rebuild
from. The column grid then came from `_coverage_gutters`, which calls a 1pt bin
a gutter below 12% of peak coverage. On this page the real gutter between the
left description and the left rate sits at 4–5 runs against a peak of 18 — it is
not empty, because the descriptions run to x 252.96 and two page-wide titles
cross the sheet. All four missing boundaries were bins the histogram called
occupied.

**The fix asks the unambiguous question instead: where does a *cell* start.**
`guides.table_columns` clusters the x at which lines begin cells and keeps a
column only where at least two lines agree. `emit.py` now takes the table grid
from it. `flow` — the dissolved reading columns the prose path uses — still
comes from `_column_bands`, so no prose region moves and no `_is_prose` verdict
changes.

**Three bundles changed and only three**: `2551m-2002`, `0605-1999`,
`extra/2200an-2018` (`git status -- forms/`). 0605's tax-type table and its
two-column Guidelines are now real columns (F168, F169 closed on the same
measurement), and 2200-AN's Schedule 1A now binds `XG021 | Up to P600,000 | 4%`
across three cells where it used to merge all three into one.

**A declared blast radius of two was wrong, and the reason is worth keeping.**
The separate reflow track measured "exactly two bundles change" over a rebuild
that called `emit.main` without `--guide-source`. That flag is what converts a
standalone guide PDF into reflowed text, and it is the only path 2200-AN's
tables come from — so that rebuild never exercised the case that moved. A
blast-radius measurement has to use `batch.py`'s own argv.

## The reflow was silently dropping text, and nothing had ever noticed (F182)

Found by landing the change above, and it is the more serious of the two
defects because it was losing content rather than misplacing it.

`_table_markup` gave each cell a colspan equal to the number of grid columns
its widest run overlaps, then walked the row with `index += span`. When a run
crossed into a column that a **later run on the same line started in**, the walk
stepped straight past that column's index and the cell was never emitted — its
runs left the document, with the row still well formed.

- **How it surfaced:** `emit.py --self-test`, "a converted guide PDF carries
  every run of its own extraction" — `310 runs, 21 missing` — the moment the
  r19 grid made columns narrow enough to expose it. That check has been in the
  file all along; the old grid was simply too coarse to trip it.
- **What was shipping:** `forms/extra/2200an-2018/guide.html` was missing
  **`(To Part III, Item 16)`** — the pointer telling a filer where Schedule 1C's
  total goes — plus two `t` glyphs. Dense-character diff across the fix: **three
  insertions, zero deletions, 13,228 → 13,248**.
- **The fix** clamps a colspan so it cannot reach a column that owns a cell of
  its own. Content is never dropped; only the span narrows.
- **Isolated:** rebuilding 2200-AN with and without the clamp gives identical
  table shapes (79 and 74 rows either way); the clamped build is 836 bytes
  larger. So the shape change is the grid, and the 836 bytes are the text that
  was being lost.
- A new unit assertion in `emit.py`'s self-test now drives the exact shape,
  independent of any corpus form: *a run crossing into an occupied column does
  not swallow its cell*.

## Gate — full clean-tree run r20 (2026-08-07 18:27, `73c3ce4`)

    PASS  self-tests 10 · conversion 53/53 · rules 53/53 · paper 53/53
    PASS  artwork 53/53 · text 53/53 · tracked-files · audit-refresh 53
    PASS  determinism  byte-identical (b5e4f9e1b979, moved from 7a152bc88161 —
                       25 form documents changed and had to. Two generations
                       still compare byte-for-byte)
    FAIL  assertions   inputs_over_printed_text 20/53        (r19: 20, unmoved)
                       comb_slots_match_printed 22/53        (r19: 22, unmoved)
                       inputs_span_no_printed_divider 11/53  (r19: 11, unmoved)
                       printed_box_peers_all_fillable — GONE from this list
                                                        (r19: 14/53)
    FAIL  findings     42/128 blocker+major open  (r19: 55/126)
    UNEV  comb-referee 52/53 forms, 2551Q the only error — identical to r19

**9 of 12, the same three checks red as r19, and one assertion fewer inside
the red one.** `printed_box_peers_all_fillable` no longer appears in the
`assertions` detail at all: that is the full clean-tree gate confirming the
14 → 0 measurement.

**Re-run to confirmation at `e7416c8` (2026-08-07 19:20), after both faults
below were fixed.** Same 9 of 12, same three red, and each red one now reading
its honest value:

    FAIL  assertions   3 of 10 (the same three)
    FAIL  findings     42/128 blocker+major unresolved   (was UNEVALUABLE)
    UNEV  comb-referee report is partial: 52/53 forms    (was 27/53)
    PASS  determinism  byte-identical (b5e4f9e1b979) — the SAME digest as the
                       18:27 run, so the corpus under measurement did not move
                       between them

**Two faults in this run were mine, both self-inflicted, both fixed, and both
worth recording because each cost a 60-minute run:**

1. **`findings` came back UNEVALUABLE, not FAIL** — "finding 184 schema is
   unsupported". `FINDING_KEYS` is an exact set and my two new entries omitted
   `resolution`; their `cause` also has to be one of the declared
   `cause_codes`, which cannot be extended because `cause_codes` is inside the
   immutable-baseline digest. Both now carry `resolution: ""` and `cause: C5`,
   and `gate.py --only findings` reports the honest **FAIL 42/128**.
2. **`comb-referee` fell to 27/53** — "emitted HTML bytes changed from the
   reviewed pin" on 25 forms. `EXPECTED_HTML_STRUCTURE_SHA256` hashes
   **`build/html/<slug>.html`, the emitted document**, and I refreshed it from
   `forms/<slug>/index.html`, the bundled one. Corrected against the right
   artifact and verified by re-running the referee: **52 of 53 forms, one
   error, and it is 2551Q's reviewed control (`p2c5 != 14`) — byte-for-byte
   r19's position.** 2551Q's own documents are unchanged at r20, and the
   reviewed pin was NOT moved. Exactly the same 25 slugs differ at the emitted
   and the bundled level, so the tag/attribute review below covers the right
   population; only the artifact being hashed was wrong.

## The four user-visible checks, looked at rather than counted (r20)

Screenshots in the session scratchpad under `blockers/`, taken with Playwright
against the shipped `forms/` tree over a local static server, 3× device scale.

| Check | Verdict | Evidence |
| --- | --- | --- |
| 0619-E item 3 "Amended Return?" YES is tickable (F152) | **YES** | `F152-0619e-item3-amended-yes.png` — an X typed into the YES box. Input `p1c22-i` in cell `[275.94, 134.49, 289.08, 145.98]`, which is the assertion's offender box `[276.05, 134.64, 289.08, 146.16]` |
| 2550Q item 3 second-quarter box is tickable (F177) | **YES** | `F177-2550q-item3-quarter-2nd.png` — X in the 2nd box, all four quarters present. Input `p1c11-i` at `[470.4, 110.1, 484.1, 122.3]` |
| 2316 item 3 TIN shows 14 character boxes (F111) | **NO — and unchanged** | `F111-2316-item3-tin.png`. The row is one 37.92pt free-text input + combs of 3, 3 and 5 = **12** boxes where the sheet prints 3-3-3-5 = 14. The first group is the uncombed one. Byte-compared against `HEAD:forms/2316-2021/index.html`: the slot census of that row is **identical** — same four containers, same 3/3/5, same widths. F111 stays open; r20 neither fixed nor worsened it. (The finding's "8" is itself stale: HEAD renders 12, not 8.) |
| 0605 "BCS No./Item No. (To be filled up by the BIR)" is not a taxpayer input (F147) | **NO — and unchanged** | `F147-0605-bcs-bir-only.png`. `p1c17`, 254.51 × 18.96pt at (321.61, 185.88), one free-text input, holds the X. Identical at HEAD, same cell, same rect, one input. **The check as posed is false at HEAD too** — the box has always been fillable, which is what F147 (blocker, open) says. Not worse; not better |

## Corpus census — r20

Re-derived from the regenerated `build/layout` and the shipped `forms/` tree.
**Six census pins moved and one of them was already wrong at HEAD** — r19 took
`comb_referee.EXPECTED_COMBS` to 4,538 and left `gate.EXPECTED_COMB_SUBJECTS`
at 4,521, so `validate_comb_referee_report` was comparing 4,538 against 4,521
and could only ever have failed. That is G01 repeating in the same pair of
files one revision later. Both now say 4,543.

| Quantity | r20 | r19 | Note |
| --- | --- | --- | --- |
| Bundles / unique codes | 53 / 50 | 53 / 50 | unchanged |
| Pages | 116 | 116 | |
| Lattice cells | **20,704** (10,050 `field`) | 20,688 (10,002) | +16 cells, +48 `field` |
| Comb ledger subjects | **4,543** | 4,538 | six slugs move: 0605 21→19, 1600WP 16→17, 1604CF 12→15, 2550M 23→21, 2551M 15→18, 2553 16→18 |
| Retained (suppressed) subjects | **21** | 17 | same six slugs |
| Active comb cells | 4,522 | 4,521 | derived, never a literal |
| Emitted inputs | **45,643** | 45,583 | +60, and **nothing deleted** |
| Comb slot divs | **40,017** | 40,008 | +9 |
| Comb slots with no input | 281 | 281 | unchanged — the compartments the source already filled in |
| Form documents changed | **25 of 53** | — | plus `forms/index.html`; **0 guide documents** |
| Assertions demanded by the gate | 10 | 10 | unchanged |
| Findings | **185** | 183 | **42 blocker+major open of 128** (was 55 of 126) — 15 closed on measurement, F184 and F185 filed open |

**The 25 changed documents were reviewed, not rubber-stamped**, before their
`EXPECTED_HTML_STRUCTURE_SHA256` pins were refreshed (the other 28 are
byte-identical and were not touched). Tag inventory moves in one direction —
**+60 `<input>`, +29 `<div>`, +3 `<rect>`, zero elements deleted** — and
visible text is **token-for-token identical in every document**; the only
text-length changes, 2550M +3,767 and 1604CF −1, are entirely inside the
embedded band-data `<script>`. The three new rects were checked against the
sheet rather than counted: all three are 2550M page 2 at x 574.92, the last
three segments of the right-hand column rule, whose mirror at x 452.28 was
already painted at HEAD. Every other rule moved by exactly half its stroke
width at a capped end and by nothing at a butt-capped one.

## Corpus census — r19

**Exactly one census pin moved, it is a guide-document count, and it moved for
a declared reason.** r19 changed `emit.py` (the table grid and the colspan
clamp) and `guides.py` (the new `table_columns` producer and its self-test).
Neither touches the lattice, the IR, the layout or the form document, so every
*form*-side census is unchanged and was predicted to be before the run.
`batch.py` re-converted 53/53; `git status -- forms/` names three guide
documents and nothing else, and `forms/index.html` regenerated byte-identical.

The comb census pin changed shape too, but not because anything generated moved
— see "the ledger denominator" below: `EXPECTED_COMBS` is the *subject*
denominator (4,538) and the *active comb cell* count (4,521) is now derived from
it rather than confused with it. Re-derived from the fresh `build/layout` for
all 53 forms: 4,538 subjects, 4,521 active, 17 retained, and every per-slug
retained pin matches.

| Quantity | r19 | r18 | Note |
| --- | --- | --- | --- |
| Bundles / unique codes | 53 / 50 | 53 / 50 | 38 direct + 15 under `forms/extra` |
| Pages | 116 | 116 | |
| Lattice cells | 20,688 (10,002 `field`) | 20,688 | unchanged — r19 is guide-side only |
| Comb ledger subjects | **4,538** | 4,521 (pin was wrong) | the `EXPECTED_COMBS` denominator: active + `retained_unresolved` |
| Active comb cells | 4,521 | 4,521 | unchanged; now `EXPECTED_ACTIVE_COMBS`, derived, never a literal |
| Retained (suppressed) subjects | 17 | 17 (uncounted) | now pinned per slug in `EXPECTED_RETAINED_SUBJECTS_BY_SLUG` |
| Emitted inputs | 45,583 | 45,583 | unchanged |
| Comb slot divs | 40,008 | 40,008 | unchanged |
| Comb slots with no input | 281 | 281 | the compartments the source already filled in |
| Editable slots on a short pre-printed constant | 0 | 0 | G11's own metric |
| `mixed` cells still carrying an input | 156 of 180 | 156 of 180 | correct — money combs, printed ink is the decimal decoration (C4) |
| Assertions demanded by the gate | 10 | 10 | unchanged at r19 |
| Guide documents changed | **3 of 36** | — | `2551m-2002`, `0605-1999`, `extra/2200an-2018` |
| Findings | **183** | 181 | **55 blocker+major open of 126** (was 59 of 125) — F127/F167/F168/F169 closed on measurement, F182 filed and fixed, F183 filed open |

## The comb referee: four defects in the referee itself, none of them a producer regression (r19)

The referee has scored `UNEVALUABLE` on every run it has ever made. r19 lands
four fixes to the **referee's own** derivation. None of them weakens a check;
three of them make the referee ask for *more* than it did.

**1 — One tolerance was pinned to five relations that do not share it.**
`validate_audit_position_evidence` demanded `HTML_GEOMETRY_EPSILON_PT`
(0.0002) from all five published position relations. Two of them
(`emission_layout_position`, `emission_layout_outer_position`) compare two of
our own four-decimal serialisations and really are exact to 0.0002. The other
three carry `source` in their names and cross into raw source geometry;
`audit.py` binds exactly those three to `POSITION_TOL_PT` (0.25) and documents
why at its declaration, and `comb_referee.py` already carried the same 0.25
under the same name for its own Poppler work. So **every offender the audit has
ever published failed to parse**, was dropped from `dimensions_by_cell`, and the
re-derived partition collapsed to zero — producing the tolerance error plus two
downstream errors. Each relation is still pinned to exactly one fixed constant;
swapping them in either direction is still rejected. `git log -S` shows the code
unchanged since the landing commit `abb0c1e`; the referee's own self-test
fixtures published 0.0002 on all five fields, a record no producer emits, which
is why it never fired.

**2 — The ledger denominator was moved by a count of a different thing.**
r14 measured comb *cells* (4,521) and subtracted the difference from
`EXPECTED_COMBS`, which is the *subject* denominator — compared against
`len(published_subjects)` and `len(cells)`, both of which enumerate
`retained_unresolved` subjects too. A comb that stops being a writing surface
does not leave the ledger; that is the ledger's whole purpose. **Bisected:**
running `21e0630^`'s `lattice.py` over the unchanged `build/ir` for all 53 forms
yields a ledger identical to HEAD's, form for form — 4,538 subjects, 4,521
active, 17 retained. `21e0630`'s shaded-paper fix moved neither census. Exactly
two subjects were genuinely stale (1700-2018, 143 → 141), and they were already
stale before it. The two quantities are now pinned separately and the active
count is *derived*, so they can never be added or subtracted from each other
again.

**3 — Mixed paper was refused outright.** `bind_artifacts` demanded
`paper.uniform is True`, which failed 1604-CF — whose page 3 really is landscape
in the pinned source (`pdfinfo`: 612x1008, 612x1008, **1008x612**, 612x1008).
A form the referee cannot evaluate scores the same as a broken one. The paper
contract is now bound per page against an exhaustive, canonically ordered
`distinct_sizes` inventory, with `uniform` required to be the true derived
claim — **strictly more than the old check asked of the 52 uniform forms**: a
false `distinct_sizes` used to pass and now does not.

**4 — Named `@page` rules read as grammar violations.** A mixed-size document
emits one `@page page-N` per page plus a `.page-N{page:page-N}` binding; the old
contract demanded a single page size outright, so 1604-CF's four correct named
rules read as thirteen violations — and `slot_records` folds
`invalid_bindings` into every cell's `valid`, so all ten of its combs were
published as emission disagreements they are not. A uniform document must now
carry **no** named rules, and a mixed one exactly one per emitted page bound to
that page's own geometry and its own selector.

## The field layer stopped being invisible — G10's first two assertions (r18)

This is the increment's whole point, so its numbers come first.

**Why:** 171 of 172 ledger findings carried `audit_blind: true`. The 51-form
visual sweep found 138 defects and **137 sat on pages this gate scored rules
100% / text 100% / 0 missing / 0 extra**. The two existing assertions that come
closest each take their candidate population from the producer that made the
mistake, so the mistake removes its own members from the population:
`money_boxes_have_inputs` enumerates from `b.layout_cells` and accepts only
`kind == "field"` (a `field` cell with zero inputs occurs **0 times in 9,971** —
that is the mechanism, not a clean bill of health), and
`comb_slots_match_printed` opens with `if b.layout is None` and inventories the
layout's comb subjects. Neither of the two new assertions reads `b.layout`,
`b.plan`, `build/layout/*.json`, emit.py's markers or the IR. Their whole
expectation comes from the pinned PDF's own composited paint stream
(`ordered_vector_paints`) and its own text operators (`drawn_glyph_boxes`),
scored against `input_boxes(cell)` from the emitted DOM.

| New assertion | Forms failing | Offenders | Denominator |
| --- | --- | --- | --- |
| `inputs_span_no_printed_divider` | **11 of 53** | **79** | 44,536 emitted inputs walked |
| `printed_box_peers_all_fillable` | **14 of 53** | **14** | 7,223 printed boxes recovered from the source |

**These are newly-VISIBLE defects, not new defects and not a regression.** Every
one of the 93 offenders was already in the shipped corpus at r14, r15 and r17;
what changed is that a check can now see them. An assertion that catches real
defects on day one is the point of writing it.

**The strongest evidence that they measure the right thing is that they land on
findings a human found by eye, at the same coordinates.** `printed_box_peers_all_fillable`
reports 0619-E's offender at box `[276.05, 134.64, 289.08, 146.16]`; F152, filed
by a reviewer on 2026-08-07, records "the printed box is at (276.0, 135.0)
12.5 x 10.5 pt". 0620 matches F153 the same way. Both are blockers, both were
`audit_blind: true`, and neither is blind any more. `inputs_span_no_printed_divider`
reports 2550M `p1c2` at `[209.28, 90.72, 270.00, 102.48]` spanning three printed
dividers — the case STATUS.md has carried since 2026-08-06 as G02a, diagnosed by
hand against `lineCap`, and until now invisible to every gate check.

**Nine of the 93 offenders were on populations no open finding covered, and the
ledger now carries them: F173–F181** (5 dead checkboxes, 4 comb-spanning input
groups). The rest map onto open findings already filed — F152, F153, F106, F135,
F150, F049/F054/F058/F062, F041, F073, F111, F115, F163, F164, F165, F166 — so
the two assertions independently re-derive 16 existing human findings from the
source PDF alone.

The five dead checkboxes are worth naming because each one makes a legally
required election unstateable:

| Finding | Form | The box that cannot be ticked | Peers on the same printed row that can |
| --- | --- | --- | --- |
| F173 | 1701 | ATC **II016 Mixed Income – 8% IT Rate** | II011, II015, II017 |
| F174 | 1701MS | item 17 spouse **Optional Standard Deduction** | the taxpayer's identical OSD box |
| F175 | 1706 | item 11 International Tax Treaty **No** | item 11 Yes, item 10 Yes/No |
| F176 | 2200M | item 12 Special Law / Treaty **No** | item 12 Yes |
| F177 | 2550Q | item 3 quarter **2nd** | Calendar, Fiscal, 1st, 3rd, 4th |

Each was confirmed against the pinned source's own text operators before it was
filed: the label immediately right of the offending box was re-derived from the
PDF, so "the dead one is the 2nd quarter" is a measurement and not a guess.

### What the two assertions deliberately refuse to say

Both are narrow on purpose, and the narrowness is the reason to trust them.

- A9 counts a divider only when it is dark (tone ≤ 0.5), thin (≤ 1.6pt),
  materially taller than wide, **still visible after the page composites**, more
  than 0.5pt inside BOTH of the input's own edges, and sharing ≥ 1pt of the
  input's height. The visibility clause is not decoration: 2550M draws a comb
  tick and then paints a white 44 × 13pt rectangle over it, and dropping the
  clause inflates the count from 79 to 111 with 32 dividers that are not on the
  printed page at all.
- A9 reports the **input**, not the divider. 2550Q `p1c41` is one 437pt input
  over 30 printed compartments; publishing 30 rows would bury the one defect.
- A10 stays silent on a row where **nothing** is fillable. Such a row may
  legitimately be Bureau-only, and guessing there is exactly what would make the
  assertion untrustworthy. It speaks only when the sheet itself has already said
  these boxes are the same kind of thing, by giving at least one of them an
  input.

### gate.py had to change too, and it was declared in one commit

`gate.REQUIRED_ASSERTIONS` and `gate.BASIC_ASSERTION_COUNT_FIELDS` are exact
allowlists: an assertion name the gate does not know makes the record
`unsupported basic assertion`, and a published count field it does not know makes
it `detail has unsupported fields`. Both grew by two, the synthetic fixture
`_synthetic_audit_record` declares both new keys' counts, and the self-test's
literal `8` became `10` **plus a new invariant** — every non-comb name in
`REQUIRED_ASSERTIONS` must have a declared count contract — so the next agent who
adds an assertion without declaring its contract fails a 3-second self-test
instead of a 60-minute gate. That is G17's lesson paid forward rather than
restated.

`comb_referee.AUDIT_PRODUCER_SHA256` re-pinned `d31b4d7a` → `8d22a957`, with the
reasoning recorded at the constant: the new assertions add derivation the referee
does not adjudicate and touch no existing assertion's code path.

## PT 060 still reads 5%. It is officially 2%. NOT FIXED at r18. (SUPERSEDED by r19 — kept as the record of two failed landings)

**Report this loudly rather than quietly: the guide reflow fix did not land, so
2551M's ATC table still binds the wrong tax rate to the wrong ATC code.** The
work was done and measured in a separate track and was explicitly reported as
`fixed: false`; `guides.py` is byte-identical to r14 at r18 (`git log
-- tools/formgen/guides.py` ends at `1e4da29`, a census pin), and the shipped
bundle proves it.

Measured at r18, two ways that do not share a producer:

| Source | PT 060 |
| --- | --- |
| `forms/2551m-2002/guide.html`, first `gl-table` | description cell `"Franchises on electric utilities, gas and water utility 2% performing quasi-banking functions"`, **Tax Rate column `5%`** |
| Pinned PDF `2551m.pdf` sha256 `f678be68…` (matches `provenance.json`), page 2, PyMuPDF text operators | row y 202.0–210.0 is ONE scanline carrying TWO source rows: `PT 060 … water utility` with its rate **`2%` at x 251.5** in the LEFT rate column, and `PT 112 2) On interest, commissions and discounts paid from their loan … 5%` at **x 549.1** in the RIGHT rate column |

The reflow has no column detection, so it binds the right half's 5% onto the
left half's code. **A reader picking an ATC from this table can file a franchise
tax at 5% where the statute says 2%.**

**F127 is therefore REOPENED**, with the retraction recorded in its own
`resolution` field. The 2026-08-06 closure measured the symptom it named — prose
flattening, which really is gone — and declared the finding fixed while the
association the finding says was destroyed is still destroyed. The assertion that
closed it, `reflow_rate_without_description`, is structurally unable to see a rate
bound to the wrong code: it asks whether a row has a rate and no description, and
this row has both. F167 (blocker) carries the same defect as its own row and
names `guides.py`'s reflow as the owner. The blocker+major count moved 49 → 59
partly for this reason, and a count that goes up because a wrong `fixed` was
retracted is the ledger working.

## Gate — r19 (2026-08-07 15:41, `d3e7a72`, clean tree) — 9 of 12 PASS

**The authoritative run.** Same verdict count as r18 and the same three checks
red. **No regression on any check, and the referee moved a long way.**

| Check | r19 | r18 | Detail |
| --- | --- | --- | --- |
| self-tests | PASS | PASS | 10 modules (11 run by hand, `validate_tree` included) |
| conversion | PASS | PASS | 53/53 unique tracked forms |
| rules | PASS | PASS | clean on 53/53 |
| paper | PASS | PASS | exact on 53/53 |
| artwork | PASS | PASS | clean on 53/53 |
| text | PASS | PASS | clean on 53/53 |
| assertions | **FAIL** | FAIL | `inputs_over_printed_text` **20** (r18: 20); `comb_slots_match_printed` **22** (22); `inputs_span_no_printed_divider` **11** (11); `printed_box_peers_all_fillable` **14** (14). **Not one of the four moved by a single form** — r19 is guide-side and the assertions are form-side, which is the prediction and the confirmation |
| findings | **FAIL** | FAIL | **55/126** blocker+major unresolved (r18: 59/125). Worst: 1701 5, 1701MS 3, 1707 3, 2553 3 |
| tracked-files | PASS | PASS | no tracked deletion |
| audit-refresh | PASS | PASS | fresh audit atomically published for 53 forms |
| determinism | PASS | PASS | byte-identical, **`7a152bc88161`** (r18/r15/r14: `8ceeab9e506d`). The digest MOVED and had to: three guide documents legitimately changed. Two generations still compare byte-for-byte |
| comb-referee | **UNEVALUABLE** | UNEVALUABLE | **52/53 forms, up from 40/53** — the four referee fixes above cleared twelve. One form still does not arrive: **2551Q**, and it is not a form defect. See below |

**The three red checks are the three that were red at r13, r14, r17 and r18.**
The two counts r19 could plausibly have disturbed — the four assertion
populations — did not move by a single form, which is what a guide-side change
should do and is the check that it did nothing else.

### The one form the referee still cannot report, and why the pin stays put

`2551q-2018` raises `RefereeError: 2551Q reviewed control changed: p2c5 != 14`.
`REVIEWED_2551Q_EXPLICIT_COMPARTMENTS` is a **human-reviewed** control: p2c5 was
reviewed as `measured` with 14 compartments. The referee now returns

```
status      unevaluable
reason      source topology does not occupy a strict majority of the full comb band
contract    y 108.26–125.96, span 17.70pt
measured    6.96pt of that span; 10.74pt unmeasured
```

p2c80 (reviewed at 12) is refused the same way, 7.44pt measured of 18.78pt.

**The pin was not moved and must not be.** Moving a reviewed control to match
the producer that stopped satisfying it is the exact failure mode this project
has already paid for twice (`EXPECTED_COMBS` at r14, `HTML_RUNTIME_SCRIPT_SHA256`
at G17). It is filed as **G18**.

**It is not r19's doing.** r19 changed `emit.py` and `guides.py` on the guide
path only; 2551Q's `index.html`, its layout and its IR are byte-identical
(`git status -- forms/` names three guide documents and nothing else), and the
referee's verdict here is derived from the pinned PDF's Poppler geometry and the
layout. What r19 changed is that 2551Q now *reaches* this check — the same
"newly visible, not newly broken" shape as r18's two assertions.

**Reaching PASS is further off than one form.** With 2551Q reporting, the
referee would carry 53/53 and `combs_found` would be the full 4,538 — but
`forms_ok` is **0** and 4,385 of 4,433 subjects are `source_unevaluable`, so the
status would still be UNEVALUABLE. 52/53 buys a complete-corpus *report*, not a
score.

## Gate — r18 (2026-08-07 13:41, `191b683`, clean tree) — 9 of 12 PASS (superseded by r19 above)

**The authoritative run.** Same verdict shape as r17: three red checks, the same
three, for the same reasons on two of them and for one deliberately new reason.
**No regression.**

| Check | r18 | r17 | Detail |
| --- | --- | --- | --- |
| self-tests | PASS | PASS | 10 modules (11 run by hand, `validate_tree` included) |
| conversion | PASS | PASS | 53/53 unique tracked forms |
| rules | PASS | PASS | clean on 53/53 |
| paper | PASS | PASS | exact on 53/53 |
| artwork | PASS | PASS | clean on 53/53 |
| text | PASS | PASS | clean on 53/53 |
| assertions | **FAIL** | FAIL | `inputs_over_printed_text` **20** (r17: 20); `comb_slots_match_printed` **22** (r17: 22); **`inputs_span_no_printed_divider` 11 — NEW**; **`printed_box_peers_all_fillable` 14 — NEW** |
| findings | **FAIL** | FAIL | **59/125** blocker+major unresolved (r17: 49/116). Worst: 1701 5, 0605 4, 2551M 4, 1701MS 3 |
| tracked-files | PASS | PASS | no tracked deletion |
| audit-refresh | PASS | PASS | fresh audit atomically published for 53 forms |
| determinism | PASS | PASS | byte-identical, **`8ceeab9e506d`** — the SAME digest as r14/r15, which is the independent confirmation that no generator moved |
| comb-referee | **UNEVALUABLE** | UNEVALUABLE | 40/53, **exactly the r17 residue and no more**: `source frame/unframed partition is false` + `form audit relation contains errors` on 1604C, 1700, 1701MS, 1702EX |

**Neither pre-existing assertion count moved by a single form.** That is the
result to read twice: adding two assertions to `audit.py` did not perturb the
eight already there, and the determinism digest is character-for-character the
r14/r15 value, so the corpus under measurement is provably the same corpus.
The two new red rows are the two new assertions doing their job on day one.

**The referee's UNEVALUABLE is unchanged and still undiagnosed.** r17 named
1604C, 1700, 1701MS and 1702EX; r18 names the same four and nothing else. This
increment neither cleared it nor worsened it, and it was not expected to —
nothing here touches the referee's derivation. It stays open as G16's shadow
plus whatever the `source frame/unframed partition` complaint turns out to be.

## Gate — r14 (superseded by r18 above)

Runs r14 (04:22, `8defe23`) and **r15 (05:35, `e38672f`)**, both complete clean-tree
runs. **9 of 12 PASS** in both. r15 is the authoritative one.

| Check | r14 | r13 | Detail |
| --- | --- | --- | --- |
| self-tests | PASS | PASS | 10 modules |
| conversion | PASS | PASS | 53/53 unique tracked forms |
| rules | PASS | PASS | clean on 53/53 |
| paper | PASS | PASS | exact on 53/53 |
| artwork | PASS | PASS | clean on 53/53 |
| text | PASS | PASS | clean on 53/53 |
| assertions | **FAIL** | **FAIL** | `inputs_over_printed_text` 20 forms (was 40); `comb_slots_match_printed` 36 forms (was 22) — see below |
| findings | **FAIL** | **FAIL** | 49/116 blocker+major open (was 58/116) |
| tracked-files | PASS | PASS | no tracked deletion |
| audit-refresh | PASS | PASS | fresh audit atomically published for 53 forms |
| determinism | PASS | PASS | byte-identical (`8ceeab9e506d`) |
| comb-referee | **UNEVALUABLE** | UNEVALUABLE | 40/53. r14's cause (a third stale pin) is fixed; r15's residue is G16's shadow — see below |

The verdict shape is unchanged from r13: the same three checks are red, for
reasons that moved in the intended direction on two of them. r14 is the first
full gate run on this branch.

### The referee's UNEVALUABLE, and the pin nobody had counted

r14 reported `form emission binding has errors` on 0619E, 0619F, 0620, 1600-PT
and 1600-VT, with the payload reason **"HTML runtime scripts disagree with the
reviewed emitter"**. That is `comb_referee.HTML_RUNTIME_SCRIPT_SHA256`, a
**third** reviewed emitter pin — separate from `EXPECTED_HTML_STRUCTURE_SHA256`
and from the producer SHAs, read **only by the referee, which runs last**. Two
of its three hashes moved, and exactly the two the G11 fix claims to touch: the
field runtime (`positionOf` replacing attribute-indexed comb navigation, which
would otherwise stop advancing at the first printed compartment) and the field
debug overlay (F172). The band-data runtime is byte-identical, which is the
standing evidence that none of this reached page scaffolding. Re-pinned after
r14 with that reasoning recorded at the constant, and **r15 settled it**: all
five `form emission binding has errors` entries are gone and no
runtime-script complaint remains. Cost of finding it this way: one 60-minute
run, and one more to clear it. A referee-only re-run cannot substitute — it
reports `audit application envelope is stale`, because changing `comb_referee.py`
invalidates the envelope the previous run bound.

### What is left of the referee's UNEVALUABLE at r15

The report is still partial at 40/53, and every remaining complaint is
`form audit relation contains errors` (1600-PT, 1600-VT, 1604C, 1604E, 1621) or
`form audit source frame/unframed partition is false` (1604C, 1700). Those fire
on `audit_evidence.assertion_valid is not True`, and every named form is one
where compartments are now correctly refused. **The referee is UNEVALUABLE
because `comb_slots_match_printed` fails, so this is G16's shadow, not a second
defect.** Closing G16 is expected to close it; nothing else should be attempted
here first.

## The two assertions, and one of them got worse

Measured over the r14 corpus with the full `audit.py`:

| Assertion | r14 | r13 | Move |
| --- | --- | --- | --- |
| `inputs_over_printed_text` | **20 forms / 149 offenders** | 40 / 258 | **−20 forms, −109 offenders** |
| `comb_slots_match_printed` | **36 forms / 247 offenders** | 22 / 186 | **+14 forms, +61 offenders** |

The second move is a regression in the number and is **not** a regression in
the emitted forms. Splitting the 247 by the state the audit itself reports:

| `emission_state` | offenders | what it is |
| --- | --- | --- |
| `physical-slots` + `source-topology-unevaluable` | 167 | pre-existing; the audit could not evaluate the source's own comb topology. 2000-DST 30, 2200A 25, 2200P 25, 2200C 24 — none of these bundles lost an input at r14 |
| `slot-input-index-mismatch` + `invalid-emission` | **76** | **new, and caused by the G11 fix**: the audit requires a comb's input indexes to run 0..N−1 with no gap, and a refused compartment is exactly such a gap |

`audit.py` was **not** changed to accommodate this, and must not be. The
assertion is asserting an emission contract that the G11 fix deliberately and
correctly broke — a compartment the source already filled in must not carry an
input — and the fix for the number is to teach `audit.py` the new contract by
re-deriving the constant from the SOURCE PDF's own text operators, which is
where that assertion already reads from. That work is **not done**; it is
recorded as **G16** in PLAN.md. Until it is, 76 of the 247 offenders are the
check disagreeing with a change it has not been told about, and 167 are the
pre-existing population.

## Census pins were stale at HEAD, again (fixed at r14)

`comb_referee.EXPECTED_COMBS` and `gate.EXPECTED_COMB_SUBJECTS` both read 4540.
Re-running the **HEAD (21e0630)** lattice over the unchanged IR produces
**4,521**, so the pins went stale in 21e0630 itself — its shaded-paper fix
stopped 19 cells across 13 forms from being writing surface, and therefore from
being combs, without the census moving with it. This is the G01 landmine
repeating one commit later and it would have failed r14 on its own constants
after 60 minutes. Both pins, the 13 per-slug values, and `guides.py`'s
`("2550m-2007", 3)` field-cells-below expectation (1 → 0) moved at r14, each
with a comment naming its cause. `comb_referee`'s own self-test had the same
number as a **literal**; it now derives it from the pin, so that copy cannot
drift again.

## Painted walls now bound cells (this increment)

The user's complaint — a fillable box that does not fill its printed box, "the
yellow box isn't the full width", "no yellow box here" — is a boundary that the
cell grid never saw. `extract.py` files a filled rectangle as a rule only up to
`MAX_RULE_THICKNESS_PT` (1.5) and calls anything heavier an **area fill**;
`lattice.build_page` built `x_lattice` from `page["rules"]` alone, so a table
side painted as a 1.92pt rectangle never became a column. `2550M` page 2 paints
its sides at x 20.16–22.08 and 590.04–591.96 exactly that way.

The asymmetry that named the fix: `comb_boundary_candidates` had **always**
ingested structural area fills, but only for the comb path. `wall_boundaries`
(lattice.py, next to it, same fill-to-candidate shape) now feeds them to the
cell grid too, filtered by `MIN_WALL_ASPECT = 5.0`. `MAX_RULE_THICKNESS_PT` is
untouched: a wall never becomes a rule, never enters `split_verticals`, never
enters the decorative tests. **Verticals only** this increment — a horizontal
wall moves row boundaries and the growable bands measured from them.

The discriminator is measured, not guessed. Over the corpus the 997 vertical
structural fills form two populations that do not overlap on any of three
measurements: 944 **in-field dividers** (2000-OT's TIN group separators, 1707's
2.16pt marks) at aspect 2.28–4.56, and 53 **walls** at aspect 5.50–514.27.
Aspect decides because it is the scale-free measurement.

### 2550M page 2, Schedule 1 — measured against the printed grid

| | before | after | printed |
| --- | --- | --- | --- |
| page-2 `x_lattice` | 13 lines, 77.04 → 523.20 | **15 lines, 21.12 → 591.00** | walls at 21.12 / 591.00 |
| Schedule 1 col 1 (`p2c0/4/8`) | x 77.04–248.16 (171.12pt) | **x 21.12–248.16 (227.04pt)** | 22.08–248.16 = 226.08pt |
| Schedule 1 col 4 (`p2c3/7/11`) | x 448.32–523.20 (74.88pt) | **x 448.32–591.00 (142.68pt)** | 448.32–590.04 = 141.72pt |
| Schedules 6 & 8 right strip 523.20–591.00 | no cell, no input | **`p2c33/41/49`, `p2c58/66/74`** | printed column |
| inputs emitted on page 2 | 101 | **128** | — |

Emitted width exceeds printed width by 0.96pt on each side because a cell snaps
to the wall's **centre**, exactly as it snaps to a rule's centre; `emit.field_box`
then insets by the border thickness. Rasters of the before/after are in the
session scratchpad — this was checked by eye, not only by number.

### Corpus effect

Six forms changed geometry (`1604cf-2008` 111 cells, `2550m-2007` 58,
`1600wp-2010` 28, `2551m-2002` 19, `2316-2021` 6, `0605-1999` 4); seven bundles
changed bytes. 131 field cells **widened** to a painted wall, 95 field cells were
**newly created** on surface that previously had no cell at all, and 11,730pt of
writing-surface width was reclaimed.

A wall-specific census — field cells with ≥10pt of writing surface between the
cell edge and the painted wall that bounds their rows, with no lattice line in
between — moves **199 cells → 90** (7 forms), and the total lost strip width
halves, 9,938pt → 5,469pt. This instrument is *not* the 230-cell/22-form census
from the brief: that one counted all input-vs-printed-box mismatch causes,
of which thick walls were the largest population. The residual 90 sits mostly on
`1604cf-2008` (38) and `2550m-2007` (30) and is the horizontal half plus causes
this increment did not address.

43 previously-`field` cells became `label`. Every one is a narrow left-margin
strip (e.g. `1604cf-2008` p1c33, x 57.84–72.72, empty, 0 text runs) that merged
leftward to its painted wall and absorbed the printed row label already sitting
at x 30.24. Those cells were emitting an input over the right half of a label
box; not emitting one there is the correct outcome, not a lost field.

## Comb dividers lost to stroke caps (this increment — diagnosis only, no code change)

The user's complaint is that a fillable box does not occupy the printed box.
The measured instance: 2550M item 1's YYYY group prints **four** compartments,
but `p1c2` (x 208.56–270.72) emits **one** free-text input. It still does —
`kind: "field"`, no comb — because this increment changed no code.

**The tolerance fix proposed for it was refused, and refusing it was correct.**
The proposal was to make `lattice.supported_at` (lattice.py:183-187) apply
`CLUSTER_TOL_PT` on the y axis as it already does on x. Measured over the
corpus, a symmetric 0.30 flips 138 borders to combs **and 45 combs to borders**,
and does not fix 2550M anyway: its gap is 0.36. The gap histogram has a dead
zone — 0.01/0.09/0.10/0.12/0.18/0.25, then nothing until 0.34/0.35/0.36 — so no
threshold both reaches 0.36 and avoids comb→border flips (even 0.01 flips 37).
The legitimacy test in the brief is also unmet: the x test compares a point
against a horizontal's **length** endpoints, the y test against its
**thickness** band — different classes of measurement. lattice's own precedent
for y-support slack is `supported_near`, which uses `JOIN_EPSILON_PT` (0.05).

**The real cause is in extract.py, and it needs no tolerance at all.** Verified
against the pinned source (`bir2550m.pdf`, sha256 `9fb4101a…`, matching
`provenance.json`): the three ticks are stroked with `lineCap = (1,1,1)` —
**round** — at width 0.72. A round cap paints half the line width past each
path endpoint, so the ticks' real painted extent is y 99.24–102.84, and h4's
ink top edge is **exactly** 102.84. The strokes touch; the 0.36 "gap" is an
artefact of reading the path instead of the ink.

extract.py never consults the cap: there is no `lineCap`/`J` reference in the
file. Its stroke-to-rect conversion applies `half = width / 2.0` to the
**thickness** axis only — visible in the IR, where v177 spans x 223.08–223.80
(0.72 wide, half-width each side of 223.44) while its y stays the bare path
99.60–102.48. The two sites are extract.py:382 (`re` ops) and extract.py:1571
(`l` ops — the one that draws these ticks), where `near`/`far` get `half` and
`start`/`end` get raw `min`/`max`.

The fix is to extend a stroke's **length** by `width/2` when `lineCap` is 1
(round) or 2 (projecting square), and not at all for 0 (butt). That is
honouring the official geometry rather than relaxing a check — strictly more
faithful, form-code-agnostic, and it makes the 2550M contact exact instead of
approximate. It is a producer change to extract.py, so it re-pins
`AUDIT_DEPENDENCY_SHA256` and invalidates every downstream digest.

Not yet measured: how many of the 626 both-endpoints-unsupported borders have a
round or projecting cap, and therefore how far this moves
`comb_slots_match_printed`. That is the next increment's first measurement.

## The comb writing surface (previous increment)

`lattice.comb_bands` published the divider **tick** band as the comb's own
vertical extent. The tick is a guide mark under the writing box, not the box:
on 2550M's item-4 TIN row the cell walls span the full 15.60pt of the row
while the digit separators are 3.12pt stubs along its bottom edge.
`comb_writing_surface` now reports the owning cell's printed walls inset by the
cell's own border thicknesses, the same inset `emit.field_box` gives a plain
text field. The tick band stays published as `divider_band_y0`/`y1`/
`height_pt`.

| Measured over `build/layout` | before | after |
| --- | --- | --- |
| comb cells with a writing box under half their own cell | 4,474 of 4,522 | **0** |
| comb cells with a writing box outside their own cell | 225 | **0** |
| 2550M `p1c9` (item-4 TIN) slot height, in a 15.60pt cell | 3.12pt | **14.16pt** |
| 2550M `p1c9` fitted face | 2.81pt | **8.25pt** (the sheet's modal body size) |

The change is vertical only, and the regenerated bytes prove it: 0 slot counts,
0 pitches and 0 slot X positions moved anywhere in the corpus, and no comb count
moved on any bundle, so `EXPECTED_COMBS_BY_SLUG` needed no re-pin.

## Failing assertions (corpus-wide `--assertions-only`)

| Assertion | r6 forms / offenders | r7 forms / offenders | Movement |
| --- | --- | --- | --- |
| `inputs_over_printed_text` | 40 / 239 | 40 / **258** | **worse by 19 offenders**, same 40 forms |
| `comb_slots_match_printed` | 22 / 186 | 22 / 186 | unchanged, identical form set |
| `money_boxes_have_inputs` | 0 / 0 | 0 / 0 | holds |
| `rules_below_guide_cut` | 0 / 0 | 0 / 0 | holds |
| `run_colour_matches_ir` | 0 / 0 | 0 / 0 | holds |
| `reflow_rate_without_description` | 0 / 0 | 0 / 0 | holds |
| `image_transform_applied` | 0 / 0 | 0 / 0 | holds |
| `no_invented_codepoints` | 0 / 0 | 0 / 0 | holds |

**`inputs_over_printed_text` got worse, and it is the writing-surface fix that
did it.** 17 comb cells newly overlap a printed run and 1 stopped; every one of
the 24 new offender records is a comb cell, and the overlapped runs are the
captions those cells carry in their upper half (`2316-2021` p1c27-30 "Date of
Birth"/"(MM/DD/YYYY)", `2551m-2002` p1c76/81/88 "28C"/"29B"/"30C",
`2553-1999` p1c71/76/83, `2550m-2007` p1c91-93 "Debit Memo",
`1604cf-2008` p1c16/21 "Telephone No."/"Zip Code", `1600wp-2010` p1c24,
`1701ms-2024` p1c183).

The cause is real and is named rather than absorbed: in those cells the lattice
rectangle spans caption **and** comb (e.g. `2551m-2002` p1c76 is 18.65pt tall
with its ticks in the bottom 2.88pt), so "the whole cell inset by its borders"
reaches text that the 3.12pt band never touched. Both readings are wrong for
these cells; the new one is wrong in the direction where a taxpayer can
actually type. No gate verdict changed on it — the check failed on 40 forms
before and after — but the offender count is a debt, and the fix belongs in
lattice.py's cell segmentation, not in relaxing the assertion.

## Findings ledger (`review-findings.json`, 183 findings) — r19

| Severity | Open | Resolved | Total |
| --- | --- | --- | --- |
| blocker | 15 | 22 | 37 |
| major | 40 | 49 | 89 |
| minor | 39 | 4 | 43 |
| cosmetic | 12 | 2 | 14 |

The gate counts blocker+major only: **55 open of 126** at r19 (r18: 59 of 125).

Moved this increment, each on a measurement recorded in the finding's own
`resolution`:

| Finding | Severity | r18 | r19 | On what |
| --- | --- | --- | --- | --- |
| F127 | major | open (reopened) | **fixed** | 2551M: 0 of 15 ATC codes carried their official rate → 15 of 15 |
| F167 | blocker | open | **fixed** | same measurement; the 19x4 table is 19x6 and PT 060 reads 2% |
| F168 | major | open | **fixed** | 0605 tax-type table: `QP \| QUALIFYING FEES-PAGCOR \| VT \| VALUE-ADDED TAX \| \| WG \| WITHHOLDING TAX - VAT AND OTHER`, checked against the official page 2 |
| F169 | major | open | **fixed** | 0605 Guidelines: the TIN branch-code rule is two cells per row, one per source column, instead of one zipped sentence |
| F170 | minor | open | **open** | re-measured, not carried forward: the ATC region is still two tables and the 3-line header section cannot reach `MIN_COLUMN_SUPPORT` |
| F182 | major | — | **filed and fixed** | the reflow was dropping text; 2200-AN shipped without `(To Part III, Item 16)` |
| F183 | minor | — | **filed open** | 2551M's left `Tax Rate` label sits at x 237.60 against its column edge of 251.52 |

## Comb referee — the state below is r13's and is SUPERSEDED by the r19 section near the top

The `EXPECTED_HTML_STRUCTURE_SHA256` pins were refreshed at r14, which is what
first let any form reach `audit_evidence` — and that is what made the r19
tolerance defect visible. Read "The comb referee: four defects in the referee
itself" above for the current picture; the paragraphs below are kept as the
record of what was believed at r13.

`EXPECTED_HTML_STRUCTURE_SHA256`'s 53 reviewed pins remain stale and were **not**
touched: re-pinning them is a user-review action (see `GOAL.md` `## Blocked`).
The producer pin that *is* an agent's to maintain was refreshed this increment:
`LATTICE_PRODUCER_SHA256` `9aeedba0` → `cc32ca68` for `wall_boundaries`. The
other three are unchanged and still match (`audit` `7c902be9`,
`extract` `5f75f191`, `verify` `8dbeb222`).

### Open integration question for the referee (not acted on)

`comb_referee.classify_band` reads `comb["y0"]/["y1"]` as the **source divider
band** — it seeds the open-compartment search and the attached-external-band
retry from them. `emitted_geometry_contract` reads the same two keys as the
**emitted writing box**, which is what emit.py lays out. One field now answers
two different questions. The emission side stays correct automatically; the
source side should read `divider_band_y0`/`divider_band_y1`, which lattice.py
publishes for exactly this purpose.

This was deliberately **not** changed here: the referee is the adjudicator, and
editing its derivation in the same increment as the producer change it
adjudicates is the pattern `GOAL.md`'s user decision 1 forbids. It is inert
today because the check is already UNEVALUABLE on the blocked HTML pins, but it
must be settled before the referee can score again.

## CI

Unchanged since the last measurement: the formgen job went green for the first
time on 2026-08-05 (run 31040386488). The commits in this increment have not
yet been through a CI cycle.

## Open issues, diagnosed

| Issue | State | Root cause / residual | Owner |
| --- | --- | --- | --- |
| `inputs_over_printed_text`: 40 forms / 258 offenders | worse by 19 this increment | 17 new offenders are comb cells whose lattice rectangle spans caption + comb, so the full-height writing surface reaches the caption. Residual populations A/B1/C1/C2 per the 2026-08 triage are unchanged. | `lattice.py` cell segmentation |
| `comb_slots_match_printed`: 22 forms / 186 offenders | unchanged | `printed_compartments` reads only the cell rectangle and no member of the lattice `comb` object, which is why a vertical-only change cannot move it. Residual: the still-refused U-frame-crop / corridor-absorb topologies plus genuine geometry defects. | `audit.py` topology chooser |
| Comb dividers filed as borders (2550M `p1c2` = 1 input where 4 print) | diagnosed, not fixed | extract.py ignores `lineCap`, so a round-capped tick's ink (which reaches its baseline exactly) is recorded 0.36pt short and `supported_at` correctly finds no support. Widening the tolerance was measured, refuted and refused. | `extract.py` stroke-to-rect (382, 1571) |
| comb-referee UNEVALUABLE | user-blocked | 53 reviewed HTML structure pins stale; plus the `classify_band` source/emission key collision above. | user review, then `comb_referee.py` |
| findings: 26 blocker+major open | unchanged | comb capacity (referee track), inputs-over-text populations, guide-cut orphan policy, text mis-position, individual re-verifications | per-cause owners |

## Gate r13 (2026-08-06, HEAD d74771e) — 9/12 PASS

    PASS  self-tests · conversion 53/53 · rules 53/53 · paper 53/53
    PASS  artwork 53/53 · text 53/53 · tracked-files
    PASS  audit-refresh (53 forms) · determinism byte-identical 5103254450db
    FAIL  assertions   inputs_over_printed_text 40 forms; comb_slots_match_printed 22 forms
    FAIL  findings     26/84 blocker+major
    UNEV  comb-referee 0/53 — the 51 reviewed HTML pins are stale (USER-BLOCKED)

Same three failures as r8, with the painted-wall boundaries landed. No
regression from a change that widened 131 cells and created 95.

**The result that needs explaining:** neither assertion moved. 95 new field
cells and 43 field->label conversions produced zero change in
inputs_over_printed_text (40 forms) or comb_slots_match_printed (22 forms).
A number that does not move when it should is worth as much suspicion as one
that moves wrongly — either the assertions do not measure what the fix changed,
or the fix's cells are landing outside their scope. Not yet diagnosed.
