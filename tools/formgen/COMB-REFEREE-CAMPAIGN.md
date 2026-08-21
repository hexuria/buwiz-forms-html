# comb-referee campaign scope (measured, pre-Z2)

Source: standalone `comb_referee.py` run against the r63 fresh 53-form audit,
after the three-way partition fix (`98816f72`). Payload
`7a104a4eecedf55eda29ac34e3fcad2c2069c33a087f5f0b8505e10e35b8dc19`.

## Totals

| metric | value |
| --- | --- |
| forms_expected / forms_measured | 53 / 53 |
| audit_evidence_complete_forms | 53 |
| combs_expected / combs_found | 4587 / 4587 |
| comparisons.agree | 4522 |
| comparisons.unevaluable | 65 |
| forms_ok | 24 |
| forms_unevaluable | 29 |
| forms_disagreement / forms_error | 0 / 0 |
| subjects_active / resolved / unresolved | 4557 / 4472 / 85 |
| subjects_retained_unresolved | 30 |
| ledger_blocking | 116 |
| referee_attestation_complete | false |
| status | unevaluable |

Baseline for comparison (pre-Z1): agree 4514, unevaluable 73, forms_ok 23.
The Z1 reviewed-topology registry is worth **+8 agreeing comparisons and +1
clean form**.

## The 65 unevaluable comparisons, by cause

| class | n | reason |
| --- | --- | --- |
| A | 30 | `ledger subject has no active topology for adjudication` |
| B | 3 | `audit published this subject as an offender with no printed topology` |
| C | 32 | `referee: the source does not corroborate the comb writing band` (+3 topology-proof variants) |

**Class A (30). IDENTITY NOW PROVEN, 2026-08-14.** Measured on the gate-r65
referee report by set equality, not by count: the 30 cells whose comparison
reason is `ledger subject has no active topology` are EXACTLY the 30 cells the
gate counts as `emission_layout_mismatches` (`emitted: None`,
`emitted_indexes_valid: false`), and `subjects_retained_unresolved` is 30. One
population, three names. The earlier note that only their counts matched is
superseded.

They span 17 forms: 2550M 4; 2200C, 2551M 3; 1600WP, 2000-OT, 2200A, 2200P,
2200T, 2553 2; 0605, 1604CF, 1604F, 1606, 1707A, 1800, 2200AN, 2200S 1.

**Class B (3).** 2200a-2020 p1c111, 2200c-2018 p1c107, 2200p-2020 p1c110 — the
F229 trio. Z2's outer-rail trim is expected to close these.

**Class C (32).** The referee's own writing-band corroboration. Sub-reasons,
verbatim prefixes:

- `the source walls inset this cell` — the large majority
- `the source top wall is not one w…` (2)
- `the source bottom wall is not on…` (2)
- `the layout declares no top borde…` (2)
- `the layout declares no bottom bo…` (1)
- `chosen source topology lacks a clean single-frame subject proof` (1)
- `one or more source slabs have ambiguous topology` (1)
- `source topology does not occupy a strict majority of the full comb band` (1)

## By form (22 forms carry all 65)

    7  1701ms-2024          3  1604f-2018           1  1606-2018
    6  1800-2018            3  2200a-2020           1  1706-2018
    6  2316-2021            3  2200p-2020           1  1707-2021
    5  2551m-2002           2  0605-1999            1  2200an-2018
    4  1702mx-2018c-attach  2  1600wp-2010          1  2200s-2018
    4  2200c-2018           2  1707a-2021
    4  2550m-2007           2  2000-ot-2018
    3  1604cf-2008          2  2200t-2022
                            2  2553-1999

## What full comb-referee PASS requires

gate.py:7067-7156 + elevation :6498-6757 — forms_ok 53, `comparisons.agree`
== 4587, `unevaluable` == 0, `subjects_retained_unresolved` == 0, and per-form
elevation with every cell four-way agree. Classes A and C are the campaign;
B closes with Z2.


## Gate r65: the check is EVALUABLE for the first time, and FAILS

Until r65 `comb-referee` reported UNEVALUABLE, so its own arithmetic never ran.
With the Z1 partition defects fixed it evaluates, and reports **35** — which is
34 distinct cells, one of them counted under two stats:

| cells | stat | origin |
| --- | --- | --- |
| 30 | `emission_layout_mismatches` | class A above. PRE-EXISTING, newly visible. |
| 4 | `referee_layout_mismatches` | Z2's combs. Lattice and audit AGREE; the referee dissents. |
| 1 | `referee_layout_mismatches` | 2200C `p1c6` — pre-existing, and also one of the 30. |

The four Z2 cells, all `status: stop` ("lattice and audit agree against the
independent referee"):

| form | cell | ours | referee |
| --- | --- | --- | --- |
| 1801-2018 | p1c13 | 3 | 4 |
| 2200a-2020 | p1c111 | 28 | 29 |
| 2200c-2018 | p1c107 | 28 | 29 |
| 2200p-2020 | p1c110 | 28 | 29 |

The referee recognises only a full-height WALL as a comb's outer edge and has no
concept of a rail bounded by a guide-tick run, so on these four it keeps
counting the caption region. Deferred here by owner decision 2026-08-13 rather
than fixed alongside the producer change it would vindicate: a judge is not
taught a new rule in the round that needs it to agree.

**Teaching it must be an independent measurement.** The referee parses Poppler's
vectors itself and shares no code with `lattice.py`; any tick-run rail it learns
must be derived from its own evidence and must remain able to dissent. Copying
`outer_paper_unguided`'s conclusion across would make the two implementations
one, and this check exists precisely because they are two.


## Campaign packages (R-series) — measured 2026-08-14, plan of record

User instruction 2026-08-14: "go start with it" — the campaign is now IN
scope. All numbers below are from the r65 report (`build/comb-referee.json`,
determinism `b9d71850a8c6`), enumerated per cell, not inferred.

The 66 non-agree comparisons (agree 4,521 + stop 4 + unevaluable 62 = 4,587):

| n | class | mechanism |
| --- | --- | --- |
| 30 | retained subjects | `comparison()` has NO adjudication path for a non-active ledger state — unconditional unevaluable |
| 22 | walls-inset | source walls inset the cell; band uncorroborated |
| 4 | wall-not-one-weight | 1702MX-attachment: top/bottom wall mixed weights across compartments |
| 3 | no-border-tone | 1707 p1c217, 1707A p1c207, 2551M p1c103: layout declares no border tone where the source measures a wall |
| 3 | topology-proof | single-frame proof / ambiguous slabs / strict majority — one cell each |
| 4 | stop (F232) | referee's wall-only outer-edge model dissents on Z2's four combs |

The 30 retained subjects, by suppression reason:

| n | reason codes | corroborable today? |
| --- | --- | --- |
| 18 | `emission-suppressed-no-rectangular-owner` + `painted-edge-partition` | NO — no source re-derivation exists |
| 11 | `emission-suppressed-caption-block-not-character-cells` | YES — glyph census 28–87 per compartment, already runs |
| 1 | `emission-suppressed-no-final-visible-band` | NO |

Every retained subject publishes exactly two permitted transitions:
`active_composite` or `retired_proven_false`, with
`requires_independent_evidence: true` and `blocks_gate: true`. The design
comments are explicit: the transition belongs to "whoever reviews it";
nothing in lattice.py may retire its own subject, and `validate_comb_ledger`
today refuses a ledger arriving in the retired state (proven by its own
mutation) — a certificate path does not exist yet.

### Packages, in order

- **R1 — tick-bounded outer rails in the referee (F232).** Derived from the
  referee's OWN Poppler vectors with its own clauses; it must remain able to
  dissent. Porting `outer_paper_unguided`'s conclusion across is forbidden —
  it would collapse two implementations into one. Moves stop 4 → 0 only if
  the referee's own measurement lands there.
- **R2a — source re-derivations for the two uncovered suppression criteria.**
  `no-rectangular-owner`/`painted-edge-partition`: re-derive from Poppler that
  painted edges partition the legacy rectangle (the ledger already publishes
  `mapped_partition_subject_keys` to check against). `no-final-visible-band`:
  re-derive that no final-visible band exists there. Modeled on the
  caption-block re-derivation, which is the template: reason code selects the
  question, Poppler answers it.
- **R2b — reviewed retirement.** Registry + certificate validation so a
  corroborated subject can take `retired_proven_false` ONLY with a named
  reviewer; evidence panels generated for the user's review; entries land
  after the user confirms. `comparison()` gains the adjudication verdict for
  a corroborated, review-retired subject. retained 30 → 0.
- **R3 — writing-band corroborations (29).** Per-cell measurement first;
  producer vs referee decided per class from the ink; never weaken either.
- **R4 — the three topology-proof cells.** Individual deep-dives.
- **R5 — elevation + final.** forms_ok 53, agree 4,587, unevaluable 0,
  stop 0, retained 0, four-way agree on every cell, attestation complete and
  enforceable. Gate 13/13.

House rules carried into every package: comb_referee.py is single-writer
(operator); audit.py stays locked; measure on the tree written and verify 53
forms; every new check gets a load-bearing mutation (proven by neutering);
never weaken a check or tolerance; no form-code special cases; the gate runs
alone.


## R1 — LANDED 2026-08-14

`refuted_outer_rails` in comb_referee.py: the prose refutation of edge-railed
outer regions, asked of the CHOSEN topology from the referee's own Poppler
glyph parse. Corpus acceptance against the r65 baseline: exactly the four F232
cells change (stop -> agree, 28/28/28/3, positions_match true), 4,534 cells
differ only by the declared `rail_derivation` key, all 105 reviewed 2551Q
tuples identical. Totals: stop 4 -> 0, agree 4521 -> 4525, unevaluable 62
unchanged, referee_layout_mismatches 5 -> 1 (2200C p1c6 remains -- retained
legacy subject, R2's population). Seven clauses neuter-proven; the gate gained
`_rail_derivation_errors` and re-derives every published rail basis.

Two per-slab lessons are recorded in the docstrings for whoever follows: the
glyph window must be the cell's (a comb contract band can be just the tick
row), and the question belongs to the chosen topology at final assembly --
asked per slab it regressed eleven 1701-family TIN rows.

Remaining after R1: 62 unevaluable (30 retained + 29 writing-band + 3
topology-proof) and 1 layout mismatch (2200C p1c6). R2 next.


## R2a — LANDED 2026-08-14 (`124662d4`)

All 30 retained subjects now carry source-corroboration obligations, settled
on every run: 18 partition-edge + 11 caption-block + 1 crossing-rule, proven
through `validate_comb_ledger` on all 53 layouts. 29 corroborate TRUE; the one
FALSE is 1800-2018 p1c4 (its only full-span edge has a 42.55pt void — neither
painted nor a tone boundary). The new criteria return verdict certificates and
never raise on a negative. Corpus totals byte-identical to R1's; gate r68
12/13, 31 mismatches unchanged.

## The REAL pass bar, read from gate.py (supersedes the earlier list)

`_comb_referee_outcome` requires ALL of:

    forms ok/measured/expected      53 / 53 / 53, forms_error 0
    combs expected/found/measured   4,587 each; combs_unevaluable 0;
                                    combs_source_unevaluable 0  (now 49!)
    subjects_active                 4,587  — EVERY subject ACTIVE
    subjects_active_resolved        4,587  — and RESOLVED
    subjects_active_unresolved      0      (now 85)
    subjects_retained_unresolved    0      (now 30)
    ledger_blocking                 0      (now 116)
    inferences_suppressed           0      (now 1)
    comparisons.agree               4,587; unevaluable 0
    referee/emission mismatches     0
    pending_transitions             0      (now 115)
    report errors 0; application_status ok; elevation exclusive

**The transition machinery already exists on the checking side.**
`comb_referee.transition_decision` publishes per cell:

- `active_resolved` → "none" (the 4,472)
- `active_unresolved` + agree → **"eligible-for-reviewed-resolution"** —
  "four-way evidence agrees; explicit review is still required" (the 85)
- `retained_unresolved` → **"explicit-transition-required"** (the 30)

The gate re-derives each expectation (`_transition_for_cell`) and counts
non-"none" as `pending_transitions`; elevation additionally requires every
cell's transition_status == "none". What does NOT exist is the review INPUT:
nothing can yet move a ledger resolution state, and `validate_comb_ledger`
refuses `retired_proven_false` arriving without certification (proven by its
own mutation).

**R2b therefore spans three surfaces**: a review registry + certificates
(the producer publishes states only a registry entry certifies, mirroring
W8's reviewed-topology design); referee validation of the certificate against
its own corroborations (a FALSE-corroborated subject is unretirable —
1800 p1c4 today); and the gate's ledger-binding schema for the new states.
The retained pins (EXPECTED_RETAINED_SUBJECTS_BY_SLUG) and the active
denominators move as declared census changes when transitions land.

**Newly scoped population: the 85 active-unresolved subjects.** Their
comparisons already agree (they are inside the 4,525); each needs a REVIEWED
RESOLUTION. Review load for the campaign is therefore 115 subjects, not 30.


## R2b design (2026-08-14) — reviewed resolutions and composite transitions

`active_composite` and `retired_proven_false` are permitted-transition
literals with no mechanics anywhere; the design below gives them mechanics
consistent with every pin and every doctrine already in the tree.

**The pass bar chooses the landing state.** Under `active_composite` for all
30 retained subjects, `subjects_active` reaches exactly 4,587 == the pinned
denominator with NO census change: the subject stays in the ledger and lives
on as the COMPOSITE of its mapped partition cells -- which
`validate_comb_ledger` already validates cell-by-cell against the layout.
`retired_proven_false` remains the path for a subject whose partition is NOT
real; no current subject is in that case, and the state is implemented but
expected unused.

**Two registries, reviewed data, shipped empty first** (the W8 pattern),
under `tools/formgen/review/`:

- `reviewed-resolutions.json` -- for the 85 active-unresolved subjects:
  (slug, page, cell_id) -> reviewer, date, citation, the four-way tuple the
  reviewer saw. Valid ONLY when the referee's own current run re-derives
  four-way agreement on that cell; an entry on a disagreeing or unevaluable
  cell is an ERROR.
- `reviewed-transitions.json` -- for the 30 retained subjects:
  (slug, page, legacy_cell_id) -> transition ("active_composite"), reviewer,
  date, citation. Valid ONLY when the subject's R2a source corroboration is
  TRUE on the current run; an entry for a FALSE-corroborated subject (1800
  p1c4 today) is an ERROR -- review cannot overrule the paper.

**Flow:** lattice.py consumes the registries when building the subject ledger
and publishes the transitioned state plus a bound review certificate (the
producer publishes, the registry certifies -- the producer never certifies
its own promotion). comb_referee.py validates each certificate against the
registry AND against its own evidence (four-way agreement / corroboration).
gate.py learns the new state as a DECLARED SCHEMA CHANGE: LEDGER_STATES +
`active_composite`, `_transition_for_cell` returns "none" for it, derived
counts fold it into subjects_active/subjects_active_resolved, and the ledger
binding validates the certificate shape.

**1800 p1c4** cannot transition on today's evidence. Honest paths, in order:
a connectivity-based v2 of the partition criterion (the drawn partial edges
plus the border may disconnect the rectangle even though no single full-span
edge is complete -- its probe-3 edges each score 1.0 locally), or the subject
stays pending and the gate stays honest about it.

**Packages:** R2b-1 registries + validation, shipped empty, mutations proven.
R2b-2 lattice consumption + gate schema; empty registries must leave the
corpus byte-identical and gate r-next at 12/13. R2b-3 the 115-subject
evidence-panel generator, panels delivered for user review. R2b-4 populate
per review; comparisons move; nothing else does.


## Status 2026-08-14: campaign detached from the feature branch

User decision ("go do it"): the feature ships at gate 12/13 with this
campaign as the NEXT objective. Landed on the feature branch before
detaching: R1 (tick-bounded rails, F232 closed), R2a (all 30 retained
subjects source-corroborated, 29 TRUE / 1800 p1c4 FALSE), R2b-1 (reviewed
registries, shipped empty, isolation-proof loader), and the full design.
Remaining here: R2b-2/3/4, R3, R4, R5 — with the 115 reviews batched into
two sittings as the only user-input dependency. The comb-referee check stays
computed and reported on every gate run in the meantime; its permitted
remainder is itemized in the feature branch's shipping amendment and any
motion outside that remainder fails the gate.


## C-series: the executable closure plan (2026-08-14, post-merge)

PR #14 is merged (`a4b69f17`); the campaign now runs on
`gol/comb-referee-campaign` off merged main. The full remainder is
decomposed with no unknowns left -- including the three numbers earlier
sections flagged as unscoped:

- The **49 `combs_source_unevaluable`** = the 29 band cells + 3 proof cells
  (all active, = C1+C2) + 17 retained subjects whose bands are also
  unmeasurable (8 ambiguous-slabs, 4 unsupported-SVG-geometry, 3
  strict-majority, 1 no-common-band, 1 single-frame) -- superseded by the
  composite schema in C3, not measured around.
- The **116 `ledger_blocking`** = the 115 blocks_gate subjects (30 retained
  + 85 active-unresolved) + **1 suppressed inference**: 0605-1999 `p1c177`,
  reason `no-legacy-subject` -- an inferred comb with no ledger ancestor,
  needing its own admit-or-refute review (folded into C3/C5).
- **R5/elevation is already designed and implemented**
  (`derive_application_scope_elevation`): the referee deliberately refuses
  to attest its own host runtime, and the gate replaces exactly that one
  uncertainty with the outer audit envelope -- but only once the raw
  report's sole unevaluable reason is that host attestation. C6 is the
  first time it can ever fire; that is the campaign's tail risk, bounded
  and named.

Packages C0-C7 with acceptance criteria live in the campaign worktree's
`.claude/GOAL.md` (the /goal loop's plan of record). Sequence: C0 done ->
C1 (29 band cells; the defect-finding core -- real form fixes ship to main
immediately as small PRs) -> C2 (3 proofs) -> C3 (composite machinery +
inference path; empty registries must be byte-identical) -> C4 (panels) ->
C5 (the user's two review sittings; 116 decisions total) -> C6 (elevation)
-> C7 (gate 13/13, campaign PR).


## C1/C2 outcome and the C3 fork (2026-08-14, measured)

**C1 LANDED** (`e0a65dec`, `2bbb2276`): band-uncorroborated cells 29 -> 0,
unevaluable comparisons 62 -> 33, agree 4,525 -> 4,554, source-unevaluable
combs 49 -> 20, forms_ok 24 -> 26, gate 12/13, determinism byte-identical.
Producer and referee now measure ONE relation (wall weight at the comb's
compartment midpoints). 20 writing bands moved across 5 forms.

**C2 is not a fix package.** All three cells already agree four ways; only
the referee's proof SHAPE refuses, and each refusal is a different problem:

| cell | mechanism | resolvable? |
| --- | --- | --- |
| 1604F p1c25 | 5-pitch empty gap needs the single-frame certificate; the sheet draws NO frame around that rectangle (0 elements on each of four sides, measured) | **NO** -- the referee is right; needs review |
| 1604F p1c36 | 0.48pt sliver slab carrying unrecognised candidate ink | needs its own dig |
| 2551M p2c13 | contract band 93.6pt on an 11.76pt cell -- the "divider" is a full-table column rule | clipping is the change recorded as breaking 4,417 combs |

Consequence: **13/13 is unreachable by fixing alone.** The three belong in
the review population, taking the load to 118.

## The C3 fork, settled by reading the arithmetic

`cells` is built one-per-subject from `ledger["subjects"]`, so
`combs_found == subjects == 4,587`, and PASS demands `combs_measured` and
`comparisons.agree` BOTH equal that. Every subject must therefore be
measured and agree -- including the 30. A retained subject has no comb, so
there is nothing for `classify_band` to measure. That kills the assumption
in the earlier R2b design note (that `active_composite` lands the counts
"with no census change") and leaves two honest routes:

- **A. `active_composite` + a composite MEASUREMENT.** The subject stays in
  the ledger and its measurement becomes the PARTITION rather than a comb --
  which R2a's `partition_edge_corroboration` already performs. New referee
  machinery (a composite four-way and its comparison), no census pin moves.
- **B. `retired_proven_false` + declared census reduction.** The 30 leave
  the ledger; `EXPECTED_COMBS` 4,587 -> 4,557, `EXPECTED_COMBS_BY_SLUG` and
  `EXPECTED_RETAINED_SUBJECTS_BY_SLUG` move per slug, the gate's
  `EXPECTED_COMB_SUBJECTS` follows. Simpler code, but it moves pinned corpus
  identity across three tables and two files -- the class of change this
  repo treats most carefully.

B states the truth the corroborations proved (these are not combs); A keeps
the ledger's promise that a refuted subject never leaves it. The choice is
the user's, because it is a census-identity decision, not a measurement.
