# Plan — three stages to a submittable form

Living document. Update the tables below in the same commit as the change that
moves them. Depth lives elsewhere: [ARCHITECTURE.md](ARCHITECTURE.md) (the
stages and the rules), [GOAL.md](GOAL.md) (objective, coverage, constraints),
[STATUS.md](STATUS.md) (all volatile measured numbers),
[README.md](README.md) (the pipeline itself).

**Active queue (2026-08-21, leftover-key census is on `main` as PR #28 / `df537eb`; #24–#29 are on main).**
Stage 2 TIN is PR #17. P2 is PR #18. P1/P1b is PR #20. Named Stage 1
batch is current `main` `forms/` (`0c1def60`); C01–C07 re-anchored 2026-08-20. Sitting accepted; independent verify 2026-08-19 (`verified`). `corrected-tree` PASS on this tree.
Identity catalog is PR #22–#23. Join census + remint are PR #24. Bind-to-HEAD is PR #26. R1 TIN mapper is PR #27.
Do not fold later rows into those PRs. R3/R7 joins stay closed. Uniqueness in inventory is not a join.
Windows Offline eBIRForms (saveXML / dummy Save): follow
[HANDOFF-WINDOWS-EBIRFORMS.md](HANDOFF-WINDOWS-EBIRFORMS.md) for the brief and
[WINDOWS-EBIRFORMS-PLAN.md](WINDOWS-EBIRFORMS-PLAN.md) for the serial W0–W8
queue — observation packets only, no remint, no `name=`. W9 inventory
land + R1 replay are on `gol/windows-ebirforms-land` (PR #30, unmerged).

Visual rule the user named (0605 screenshot, 2026-08-17): a **charbox** is
an outer rectangle plus short bottom hair ticks that do not run the full
height. That field must type **one character per tick**, not one `<input>`
across the whole box. A **checkbox** is a small empty square for an `X`
(the sheet says "Mark all appropriate boxes with an 'X'"). On the sitting
0605 HTML those squares are `type="text"` with **no `maxlength`** (~12×10pt
cells: Calendar/Fiscal `p1c1`/`p1c2`, item 17 `p1c43`, …). There are
**zero** `type="checkbox"` inputs on that document. Do not invent HTML
checkbox widgets; constrain them to one `X` the way the official client
does.

| # | Branch / PR | Work | Stage | Parallel? |
| --- | --- | --- | --- | --- |
| **P0** | `gol/tin-stage2` PR #17 | Even 3-3-3-5 TIN; lock `00000` only where the sheet prints `000`; outer frame + bottom hair ticks | 2 | Land independently. Do not regenerate under it until P1/P1b exist, or every TIN record re-anchors twice. |
| **P2** | `gol/tin-stage3` PR #18 (stacked on #17) | 2550M page-2 first Schedule 1 row (horizontal walls → 4-row growable); 0605 items 17 then 18 tab. Specify still a lower band. H-walls that would fuse into an existing y-line are skipped (2551M/2553 reviewed combs). | 1 / UX | Landed. |
| **P1** | `gol/tin-stage4` (this commit, stacked on #18) | Hair-tick charboxes stamp `maxlength="1"` per compartment (2550M sheets/RDO/zip; 0605 Year Ended / Return Period). Stay `type=text`. Never a TIN record. | 1 | Landed in `emit.input_is_single_character`. |
| **P1b** | same commit | X-squares (~4–20pt, aspect 0.70–1.45, plus F210 knockout interiors) stamp `maxlength="1"`. Not `<input type=checkbox>`. | 1 | Same helper; xbox size vs all-regions charbox. |
| **P0b** | `gol/tin-stage5` (stacked on #20) | Named Stage 1 `forms/` batch `ddac6058`. C01–C07 re-anchored (`p1c127` first group; P2 hairlines dropped into the knockout). `correct.py --batch ddac6058` wrote `forms-corrected/`. Sitting 2026-08-18: Uriah approved C01–C07. Independent verify 2026-08-19: `corrected_fidelity.py` 7/7. Status `verified` on the named-batch tree. | 2 | Re-anchored 2026-08-20 to current `main` `forms/` (`0c1def60`). `gate.py --only corrected-tree` PASS: 7/7 divergences named. |
| **I0** | `gol/field-identity` (stacked on `gol/tin-stage5`) | Durable field identity catalog. C01–C07 seed (28) plus 38 measured 3+3+3+5 corpus strips (152) = 180 identities. Matcher: exactly one fillable-field *center* in `source_printed_box_pt` (field or G11 mixed comb); `p1cN` is a hint. Does not write official keys onto `name=`. | 2/3 seam | Serial. TIN caption-chain class: 44/53 bundles. `extra/1801-2018` skipped at I0. Stage 3 still closed. |
| **I1** | `gol/stage3-ready` | TIN leftovers: 1801 mixed `tin-strip` + tin-2/3/branch; extra HTML 3+3+3+N chains (spouse/page-2/extra). 208 identities. Eight PDF-unmeasurable bundles still emit no chain. | 2/3 seam | Serial. Not a mapper. |
| **I2 comb** | `gol/stage3-ready` | Remaining comb fillables (4349). Roles from slot count + pitch (`date-yyyy`, `money-21`, `comb-2s`, …). Emitted box as `source_printed_box_pt`. 4557 identities. | 2/3 seam | Serial. Honest `official_field_key` gaps. |
| **I2 xbox** | `gol/stage3-ready` | X-squares (654): ~4–20pt text, aspect 0.70–1.45, `xbox-*`. 5211 identities. | 2/3 seam | Serial. Not `type=checkbox`. |
| **I2 text** | `gol/stage3-ready` | Wide text (4779) as `text-*`. Live fillable denominator 9990/9990 on both trees. Remainder 0. | 2/3 seam | Serial. Honest `official_field_key` gaps. |
| **I3** | `gol/stage3-ready` PR #23 | Remainder mint 0. Pin `EXPECTED_FILLABLE_CELLS = 9990` and `EXPECTED_UNCATALOGUED_FILLABLES = 0` in `field_identity.py` and `gate.py`. C01–C07 seed still present. | 2/3 seam | Serial. Coverage is a gate self-test, not a mapper. |
| **J0** | `gol/join-census` | Replayable join census (`join_census.py`). 9990 classified by file-existence inventory, not mint-path. Dated evidence JSON. | 2/3 seam | Serial. Not a mapper. |
| **J1** | same branch | Remint 1334 mint-path false negatives on 6 skew bundles to honest R2 gaps. Catalog size stays 9990. No keys invented. | 2/3 seam | Serial. Not a mapper. |
| **M0** | `gol/tin-map` PR #27 | Fail-closed TIN-only mapper (`map_tin.py`). 163 R1 identities: 148 writable keys copied onto input `name=` in `forms-corrected/`; 15 G11 mixed branch combs have no input (pre-printed digits) and stay counted, not rewritten. Refuses `forms/`, gapped records, stale hints, duplicate keys, missing inputs on a non-mixed field. Never invents a key. | 3 | Landed. R3/R7 still have zero keyed examples — do not join them. |
| **J2** | `gol/leftover-keys` PR #28 | Read-only leftover-key census (`leftover_keys.py`). After the 1601EQ/1701Q/2000-dst land: leftover_unique 8436, leftover_duplicate 5, claimed_unique 159, claimed_duplicate 4, claimed_absent 0, claimed 163. Never writes `official_field_key` or `name=`. Landed on `main` `df537eb`; pins retuned on the land PR. | 2/3 seam | Serial. A future harvest needs a named rule and a box role; uniqueness alone is not evidence. |
| **W0** | this Windows PC | Replay leftover pins; record live eBIRForms **7.9.6.1** vs manifest 7.9.6.0 hash drift. Dummy TIN only. Start via `/goal` + [GOAL-WINDOWS-EBIRFORMS.md](GOAL-WINDOWS-EBIRFORMS.md). | 3 evidence | First. Do not Save yet. |
| **W1** | `gol/windows-ebirforms-obs` (when landing) | Dummy Save 1601EQ Jan 2018 + 1701Q Jan 2018. Stage `serialized_key` only where saveXML emits it. | 3 evidence | After W0. Live `rules/forms/` untouched. |
| **W2** | same | Identify 2000-DST vs 2000 / 2000-OT; dummy Save; new staging snapshot. Do not let `2000-dst-2018` steal `2000-v2018`. | 3 evidence | After W1. |
| **W3** | same | 2200A tinA/B/C/branchCode occurrence → printed box. Live HTA L396 / L1390 / L2007 (not handoff L1943). | 3 evidence | After W2. No remint. |
| **W4** | same | One money comb + one growable band (prefer 2550M). Verdict `R3-shaped` / `R7-shaped` / `other`. | 3 evidence | After W3. Do not join R3/R7. |
| **W5** | same | 2550M 246 leftover_unique keys: emitted vs silent; harvest-rule proposal only if unique+boxed. | 3 evidence | After W4. |
| **W6** | same | Four leftover_duplicate keys (1702Q tel, 1707A email, 2551Q email, 2200A registeredName). | 3 evidence | After W5. |
| **W7** | same | 1600WP agent TIN (C06) only if W1–W3 done. Item 5 TIN is C05, not this comb. | 3 evidence | After W1–W3. |
| **W8** | same | Land evidence JSON (+ staging if needed) when Uriah says land. PR, no merge. | 3 evidence | Not until asked. |
| **W9** | `gol/windows-ebirforms-land` PR #30 | Landed 1601EQ/1701Q `serialized_key` and overlay inventory `tools/formgen/inventories/2000-dst-v2018` (not `rules/forms/`; that tree stays 43). Reminted 142 2000-dst no-harvest gaps to R2. Live 2000-v2018 untouched. 2026-08-21: rebuilt `forms-corrected/` from `--batch HEAD`, `--verify` PASS, identity 9990/9990, `map_tin.py --write` 163/148/15. Mapper still TIN-only. PR, no merge. | 3 inventory | After W8. Done on this branch. |

How to use agents (compact prompts, no full chat history; one writer per
file):

1. **Now, two read-only censuses in parallel** against `forms/` (not the
   overwritten sitting `forms-corrected/2550m-2007` / `0605-1999`):
   (a) every text input whose cell contains short bottom ticks;
   (b) every checkbox-sized square with no `maxlength`.
2. **One** implementer for P2 commit, then **one** for P1 charbox split.
3. Do **not** run two agents that both edit `lattice.py` or `emit.py`.
4. Identity work is `tools/formgen/field_identity.py` against
   `tools/formgen/identity/catalog.json` (9990 fillable identities,
   coverage 0 uncatalogued). Stage 3 R1 TIN mapping is `map_tin.py`; R3/R7
   stays closed even though the three preconditions hold.

Detail for P0 lives in [corrections/README.md](corrections/README.md).

**Merging PR #13 lands the pipeline and the corpus so they can be polished in
place. It does not land defect-free forms.** 26 blocker+major findings are open
and two gate checks fail. That is the starting position of this plan, not a
regression against it.

---

## Where we are

Measured 2026-08-08 at r27, worktree `.claude/worktrees/form-correction`,
branch `gol/form-correction`, corpus tagged `corpus/r27`. STATUS.md holds the
r27 census, the four user-visible screenshot checks and the gate table; the
rows below carry only the per-defect numbers.

**r27 is the first round to move `inputs_over_printed_text` without hiding it.**

| Assertion | r20 | r22 | r23/r25 | r27 | |
| --- | --- | --- | --- | --- | --- |
| `inputs_over_printed_text` | 20 / 149 | 19 / 131 | 20 / 147 | **12 / 33** | −8 forms, −114 offenders |
| `comb_slots_match_printed` | 22 / 188 | 36 / 254 | 22 / 193 | **23 / 203** | **WORSE by 1 form / 10** |
| `inputs_span_no_printed_divider` | 11 / 67 | 5 / 33 | 5 / 33 | **5 / 33** | unmoved, offender-for-offender |
| `money_boxes_have_inputs` | 0 / 0 | 4 / 4 | 0 / 0 | **0 / 0** | PASSES |
| `printed_box_peers_all_fillable` | 0 / 0 | 1 / 1 | 0 / 0 | **0 / 0** | PASSES |

r22 "improved" the first row by shrinking every comb's writing surface to a
3.12pt stub. **r27 did not**, and the proof is in the bytes rather than in the
count: all **7,405** slot rectangles surviving in the ten changed documents were
compared attribute-string to attribute-string against their r26 selves and
**zero moved**; the only rectangles removed are the 22 belonging to the eleven
refuted caption blocks. 2550M item 4's TIN is still 14.16pt in a 15.60pt row and
2316 item 3 is still 14 boxes — both documents byte-identical.

**The row that got worse is G16, and it is this fix's own measurement cost.**
Refusing 94 occupied compartments makes `comb_slots_match_printed` publish
`invalid-emission` on exactly 94 cells, because it pairs the k-th input with the
k-th compartment while `data-slot-index` already carries the compartment's true
number. 76 of the 94 were already offenders; the 18 that were not are 2200S ×16,
1800 `p1c68` and 2550-DS `p1c79`. Filed as **F192**. The assertion must not be
weakened: what it cost to leave those compartments alone was 89 taxpayer typing
surfaces laid on printed ink.

**Two new open findings and one new open class come out of r27**: F190 (a live
input over the printed caption "27 Tax Debit Memo" in the first compartment of a
29-box money comb, on 2200A/2200C/2200P) and F191 (the referee's retained-subject
contract does not know a third retained shape, so 3 forms produce no report).
Neither was patched here — F191 in particular is the adjudicator, and changing it
in the same increment as the producer it adjudicates is the failure this project
has already paid for twice.

**r23 started nothing. It paid r21/r22's three regressed assertion families,
and one of them got worse in the paying.**

| Assertion | r20 | r22 | r23 | |
| --- | --- | --- | --- | --- |
| `comb_slots_match_printed` | 22 / 188 | 36 / 254 | **22 / 193** | forms back to r20 |
| `money_boxes_have_inputs` | 0 / 0 | 4 / 4 | **0 / 0** | PASSES |
| `printed_box_peers_all_fillable` | 0 / 0 | 1 / 1 | **0 / 0** | PASSES |
| `inputs_span_no_printed_divider` | 11 / 67 | 5 / 33 | **5 / 33** | unmoved |
| `inputs_over_printed_text` | 20 / 149 | 19 / 131 | **20 / 147** | **WORSE by 1 form / 16** |

The one that got worse is reported first in STATUS.md and is **G05**, not a new
class: r22 had not fixed those 21 offenders, it had *hidden* them by shrinking
every comb's typing surface to a 3.12pt divider band that is too short to reach
a caption and too short to type in. Restoring the writing box (F186) restores
the debt with it.

The two that went green went green because the **corpus** changed, not because
a check did: `emit.comb_writing_rect` lays every rectangle the emitter draws
out on the writing box, and `audit.py`'s source-occupancy query — unchanged —
stopped being asked about a 3pt strip where the printed constant is not. That
alone took the `invalid-emission` population 64 offenders / 25 forms → 3 / 3.
The one exclusion added, `audit.source_bureau_reservations`, is derived from
the pinned PDF's own text operators, claims **exactly one box corpus-wide**
(0605 `p1c17`, blocker F147's), and publishes its own count.

**Gate — full clean-tree run r23 (2026-08-08 00:49, `912c6ed`). 9/12 PASS, the
same three checks red as r22, and TWO assertions fewer inside the red one.**

    PASS  self-tests 10 · conversion 53/53 · rules 53/53 · paper 53/53
    PASS  artwork 53/53 · text 53/53 · tracked-files · audit-refresh 53
    PASS  determinism ba1bd2d8c47e  (moved, and had to: all 53 form documents
                                     changed. Two generations still compare
                                     byte-for-byte)
    FAIL  assertions    inputs_over_printed_text        20/53  (r22: 19)
                        comb_slots_match_printed        22/53  (r22: 36)
                        inputs_span_no_printed_divider   5/53  (r22: 5)
                        money_boxes_have_inputs          GONE  (r22: 4)
                        printed_box_peers_all_fillable   GONE  (r22: 1)
    FAIL  findings      32/129 blocker+major open (r22: 33/129)
    UNEV  comb-referee  2550M p1c89/p1c90 — character-for-character r22's

**The referee is UNEVALUABLE for exactly r22's reason and r23 did not touch
it.** `p1c89`/`p1c90` are F184's cells; `lattice.py` is byte-identical this
round and the referee's derivation was not edited. It is the one thing between
9/12 and 10/12, it is r22's debt, and closing it is the reviewed
`retired_proven_false` transition F184 already names — which needs independent
evidence and a human, not an integration-time edit to the adjudicator.

**One of the four red assertion rows is green, honestly.**
`printed_box_peers_all_fillable` goes 14 offenders on 14 of 53 forms to 0 on 0
with `audit.py` byte-identical throughout. `inputs_span_no_printed_divider`
falls 79 → 67 offenders on the same 11 forms.
`comb_slots_match_printed` got **worse by 3** (185 → 188) and that is reported
in full in STATUS.md rather than netted off: two genuine position mismatches
were fixed, and five money-comb cells on 2550M now claim a compartment the
sheet does not print, which is the new finding F184.

**Corpus census — every number carries its denominator.**

| Quantity | Value | Note |
| --- | --- | --- |
| Bundles under `forms/` | 53 | 38 direct + 15 under `forms/extra` |
| Unique form **codes** | 50 | 1701 ships 3 bundles, 1702MX ships 2 |
| Codes on BIR's official list | 44 of BIR's 51 | derived from GOAL.md's 42/48 plus the two landed forms (1604-CF, 2200AN); **not re-verified against bir.gov.ph today** |
| Codes we carry that BIR does not list | 6 | 0620, 1621, 1709, 2000-DST, 2316, 2550-DS — the user asked to keep them |
| BIR codes still missing | 7 | 1600, 1601-E, 1601-F, 1602, 1603, 1704, 2000 |
| Pages | 116 | across 53 bundles |
| Lattice cells | **20,704** (10,050 classified `field`) | r20: +16 cells, +48 `field` — the two `lattice.py` fixes and the cap model |
| Emitted inputs | **45,643** | r20: **+60 and nothing deleted**. 40,017 comb slot divs, of which **281 carry no input** (unmoved) |
| Comb ledger subjects | **4,543** | the `EXPECTED_COMBS` denominator (active + `retained_unresolved`). r20: 4,543 subjects, **4,522 active**, **21 retained**, moving on six slugs. `gate.EXPECTED_COMB_SUBJECTS` moves with it 4,521 → 4,543 — it was ALREADY WRONG at HEAD, because r19 moved the referee's twin and not it |
| Form documents changed at r20 | **25 of 53** | plus `forms/index.html`; **0 guide documents**. Tag inventory across them: +60 `<input>`, +29 `<div>`, +3 `<rect>`, zero deletions, visible text token-identical |
| Gate-demanded assertions | **10** | unchanged at r20 |
| Findings in `review-findings.json` | **185** | **42 blocker+major open of 128** at r20 (was 55 of 126): 15 closed on measurement (F049, F054, F058, F062, F106, F135, F150, F152, F153, F173–F177, F180), **F184 filed open** (2550M money combs claim a compartment the paper does not print) and **F185 filed open** (11 comb-spanning inputs the cap model made visible). The 138 immutable baseline entries are untouched; the digest at `gate.py:8752` still matches, re-verified at r20 |

**Gate — full clean-tree run r20 (2026-08-07 18:27, `73c3ce4`). 9/12 PASS, the
same three checks red as r19, and one assertion fewer inside the red one.**
STATUS.md holds the full table and the two self-inflicted faults this run
found.

    PASS  self-tests 10 · conversion 53/53 · rules 53/53 · paper 53/53
    PASS  artwork 53/53 · text 53/53 · tracked-files · audit-refresh 53
    PASS  determinism b5e4f9e1b979  (moved from 7a152bc88161, and had to: 25
                                     form documents changed. Two generations
                                     still compare byte-for-byte)
    FAIL  assertions    inputs_over_printed_text 20/53        (r19: 20, unmoved)
                        comb_slots_match_printed 22/53        (r19: 22, unmoved)
                        inputs_span_no_printed_divider 11/53  (r19: 11, unmoved)
                        printed_box_peers_all_fillable        GONE (r19: 14/53)
    FAIL  findings      42/128 blocker+major open (r19: 55/126)
    UNEV  comb-referee  52/53 forms, 2551Q the only error, identical to r19

Confirmed by a second full clean-tree run at `e7416c8` (19:20) after the two
self-inflicted faults were fixed: same 9 of 12, same three red, `findings` now
FAIL 42/128 rather than UNEVALUABLE, `comb-referee` back to 52/53, and the
determinism digest character-for-character the 18:27 value, so the corpus did
not move between the two runs.

*(r19's block, superseded: determinism `7a152bc88161`; assertions 20/22/11/14;
findings 55/126; comb-referee 52/53.)*

**Three of the four assertion populations did not move by a single form, and
the fourth emptied.** The two pre-existing rows are unmoved form-for-form,
which is what makes "the checkbox class is fixed and nothing else regressed" a
measurement rather than a hope. The one number that did move the wrong way,
`comb_slots_match_printed`'s offender count, is reported in full above and in
STATUS.md, and is now finding F184 / row G19.

**The two new red rows are the point, not a regression.** Every one of their 93
offenders was already in the shipped corpus at r14; what changed is that a check
can see them. Neither pre-existing assertion moved by a single form, and the
determinism digest is character-for-character the r14/r15 value, so the corpus
under measurement is provably unchanged.

Same three checks red as r13, and no longer for stale reasons. **r14's referee
UNEVALUABLE was a THIRD reviewed emitter pin nobody had counted** —
`HTML_RUNTIME_SCRIPT_SHA256`, read only by the referee, which runs last. It is
re-pinned and **r15 confirms the fix**: all five `form emission binding has
errors` entries are gone. What remains at r15 is `form audit relation contains
errors` on exactly the forms where compartments are now correctly refused, which
fires on `assertion_valid is not True` — **the referee is UNEVALUABLE because
G16 is open, not because of a second defect.**

**That landmine is defused, and a second one of the same shape was found and
defused with it.** `EXPECTED_COMB_SUBJECTS` and `EXPECTED_COMBS` now agree at
**4,521**, which is what the lattice actually produces — both were reading
4,540, and re-running the HEAD (21e0630) lattice over the unchanged IR shows
they had gone stale in 21e0630 itself, not in this session. `guides.py`'s
`("2550m-2007", 3)` expectation and all 53 `EXPECTED_HTML_STRUCTURE_SHA256`
moved at r14 too. See STATUS.md §"Census pins were stale at HEAD, again".

---

## The three stages

    STAGE 1  GENERATE   pinned PDF -> IR -> lattice -> emit -> HTML
    STAGE 2  CORRECT    declared per-form corrections, applied after generation
    STAGE 3  MAP        fields -> eBIRForms XML payload keys

The dividing line, stated once:

> **Stage 2 is for facts the SOURCE cannot tell us. Stage 1 is for us misreading
> a source that is correct.**

A stage-1 bug moved into stage 2 buys speed now and pays forever: 53 bundles of
hand-maintained corrections re-verified on every regeneration, while the bug
still ships to every new form. Of the user's four correction items, **exactly
one is stage 2** (TIN branch-code width). The other three are traced producer
bugs.

---

## Stage 1 — generate

The working surface. One row = one defect class. Edit the row; do not rewrite
the table. `S` = status: `open` / `diag` (diagnosed, unfixed) / `fixing` /
`done`.

| ID | Symptom | Count (denominator, date) | Owning function | S | Evidence |
| --- | --- | --- | --- | --- | --- |
| **G01** | Census pins contradict each other, or contradict the producer; a full gate fails on its own constants after 60 minutes | was 4442 vs 4540; **both now 4,521 = measured** (r14) | `gate.py:80`, `comb_referee.py:86` + per-slug, `guides.py` expectation table | **done** | The second instance was worse than the first: both files agreed on 4,540 and both were wrong, because 21e0630 shipped a lattice change without its census. `comb_referee`'s self-test held the same number as a literal and now derives it from the pin. The class is not closed — one number still lives in two files |
| **G02** | Comb compartments merged into one wide input — the user's "4 year boxes as 1 big box" | **0 offenders / 53 forms** (`inputs_span_no_printed_divider`, batch `ddac6058`, 2026-08-18). Residual `comb_slots_match_printed` is **G16**, not this class. | `extract.py` `cap_extension_pt`; lattice skip-fuse | **done** | Named G02a–i findings all `fixed` or `not-a-defect`. `extra/1801-2018` tin-1 leftover is identity `tin-strip` (mixed caption+comb), not a year-box merge: 1801 holds all 10 assertions including span=0. No new `forms/` batch. |
| G02a | 2550M item 1 YYYY: 4 printed compartments, 1 free-text input | 1 cell, then 0 | `extract.py` `cap_extension_pt` | **done** | Line-cap gap was 0.36pt = round cap at width 0.72 |
| G02b | 2550-DS item 4 `Year Ended (MM/YYYY)`: 6-cell comb → 1 input | F115 | same | **done** | ledger `fixed` |
| G02c | 1701MS items 8, 10C: comb → wide input, overflows | F041 | same | **done** | ledger `fixed` |
| G02d | 2316 TIN items 3/12/16: 8 inputs for 14 printed comb cells | F111 (blocker) | same | **done** | ledger `fixed` |
| G02e | 2200C item 1 date: MM and YYYY groups have no inputs (6 of 8 cells dead) | F097 (blocker) | same | **done** | ledger `fixed` |
| G02f | 1800 item 14 centavos: free-text where every other row is 2 comb slots | F073 | same | **done** | ledger `not-a-defect` |
| G02g | 0605 items 5, 7, 9: 22 printed compartments → 8 unbounded inputs, TIN included | F163 | same | **done** | ledger `fixed` |
| G02h | 2551M item 2 `Year Ended` → 1 input; Schedule 1 period+name columns merged per row | F164, F165 | same | **done** | ledger `fixed` |
| G02i | 2550Q item 10 address line 3 + 10A ZIP → one input; line 2 of the same block is correct | F166 | same | **done** | ledger `fixed` |
| **G03** | Real field has **no** input — the user's "no yellow box here" | **0 offenders / 53 forms** (`printed_box_peers_all_fillable`, batch `ddac6058`, 2026-08-18). Diagnosis census was 160 empty non-fillable `label` cells, 38 of 53 forms (2026-08-07). | `lattice.py` cell classification | **done** | Named findings all `fixed`. Open blocker+major = 0. |
| G03a | An empty printed box is classified `label`, so no input is ever emitted. A `field` cell with 0 inputs does **not** occur anywhere in the corpus (measured: 0 of 9,971) — this is the whole mechanism | F150, F151 | `lattice.py` | **done** | ledger `fixed` |
| **G04** | Input exists where nothing should be fillable — grey spacers made FILLABLE | Named findings all `fixed` or `not-a-defect` (2026-08-18). Diagnosis census was **169 inputs** wholly on official grey decoration, 22 of 46 measured forms (1pt inset, ≥95% tone 150–240, zero black; 2026-08-07). | `lattice.py` field classification vs tone | **done** | F066, F081, F093, F095, F157–F162 `fixed`; F154, F156 `not-a-defect`. F081 (1801 grey band) holds all 10 assertions. No producer residue; no new `forms/` batch. |
| **G05** | Input overlaps pre-printed text | was 40 of 53 forms / 258 (r13), 20 / 147 (r23-r25); **12 of 53 forms / 33 offenders (r27)** | `lattice.py` cell segmentation — the rectangle spans caption **and** comb; `emit.slot_constant`; `audit.glyph_boxes` | **fixing** | r27 removed three populations at once and STATUS.md carries the split: 92 money decimal-bullet compartments that were live typing surfaces on printed ink (**F189**), 11 printed caption blocks read as 2-compartment combs — 1606 p2's whole statutory rate table and five excise mastheads (**F188**) — and the false positives, where `glyph_boxes` scored an input against the font's LINE box so every glyph was charged with its face's full descender. **Not a shrunk writing surface**: 7,405 surviving slot rectangles compared byte-for-byte, zero moved. What is left is 33 offenders on 12 forms, F134 among them |
| **G06** | Lines painted that do not exist on the official sheet | 2 open findings | extract/guides crop — barcode tail | open | F027 (1700 p1), F030 (1701, all 4 pages) |
| **G07** | Text run mis-positioned or reordered | 3 open findings | emit text placement / run ordering | open | F070 (1707A "Calendar" 4pt high); F102 (2200P header 5pt high); F060 (1702Q guide: superscript reordered, corrupts two sentences) |
| **G08** | Guide reflow orphans ATC codes from their industry | F120 | `guides.py` reflow | open | ledger |
| **G09** | Oversized leading comb slot | 29 groups at ≥1.10× median, 17 at ≥1.25× (corpus, 2026-08-06) | `lattice.comb_bands` | open | re-measured this session |
| **G10** | 137 of 138 findings carry `audit_blind: true` — the audit is structurally blind to the field layer | was 171 of 172; **the first two field-layer assertions landed at r18** and catch **93 offenders across 22 of 53 forms** — `inputs_span_no_printed_divider` 11 forms / 79 offenders (44,536 inputs walked), `printed_box_peers_all_fillable` 14 forms / 14 offenders (7,223 printed boxes recovered from the source). 9 of the 93 were on populations no open finding covered (F173–F181); the rest independently re-derive **16 existing human findings** from the pinned PDF alone | `audit.py` assertions; `gate.py` allowlists | **fixing** — the first of the two now PASSES (r20: `printed_box_peers_all_fillable` 14 forms → 0, `audit.py` byte-identical) | 0619-E's A10 offender box `[276.05, 134.64, 289.08, 146.16]` is F152's `(276.0, 135.0) 12.5 x 10.5` to the point; 2550M's A9 offender `p1c2 [209.28, 90.72, 270.00, 102.48]` is G02a, hand-diagnosed on 2026-08-06 and invisible to every check until now. Neither assertion reads `b.layout`, `b.plan`, emit.py's markers or the IR — only `ordered_vector_paints` and `drawn_glyph_boxes` — which is why they can see what `money_boxes_have_inputs` and `comb_slots_match_printed` structurally cannot. **Not `done`:** these bound two field-layer questions (a box the source drew but nobody made fillable; an input laid across a divider the source printed). "Does an input match its printed box" and "is a printed constant overtypeable" are still unbound |
| **G11** | **A cell the lattice itself marks `mixed` — meaning it knows pre-printed glyph ink is inside — is emitted with a full set of editable comb slots, so the taxpayer can type on a pre-printed constant.** `emit.py`'s `PrePrintedInk` guard (F028's second guard) applies to plain text cells only and has no effect on comb slots | **the defect's own metric is 0**: editable compartments sitting on a short pre-printed constant go **175 → 0** (r14, 2026-08-07). 281 compartments refused across 26 forms. 156 of the 180 `mixed` cells still carry inputs and should — they are money combs whose printed ink is the decimal decoration (C4) | `emit.py` `comb_slot_verdicts()`, per slot | **done** | F139–F146 all `fixed`. Verdict is per COMPARTMENT: a slot is refused when the source printed exactly one alphanumeric glyph **wholly inside that slot's walls**, or shaded it at the unchanged 0.87 threshold. Per-slot is forced by the corpus — 1600-PT prints the century in the *leading* two boxes and 1702EX the branch code in the *trailing* three, so no rule over the group tells the two apart. `II 011`, `XC 010`, `2 0` and `0 0 0 0 0` are no longer typeable; 2000-DST's money grid keeps all 14 compartments including the printed decimal bullet (C4 intact). Rasters in the session scratchpad `preprinted/` |
| **G12** | A caption and the writable blank beside it are segmented into one `label` cell, so the blank gets **no input at all**. Same root cause as G05, opposite symptom — G05 is the case where the merged cell *does* get an input | 2 confirmed (2026-08-07) → 0 open | `lattice.py` cell segmentation | **done** | F148, F149 `fixed` |
| **G13** | **A multi-column guide source is reflowed scanline-by-scanline, interleaving the columns and binding values to the wrong key.** On 2551M this puts the wrong tax rate against an ATC code | **2551M: 0 of 15 ATC codes carried their official rate → 15 of 15** (r19, measured on the written tree by a checker sharing no producer with the emitter). 3 guide bundles changed, 0605 and 2200-AN with it | **`emit.py`** `reflow_page` → `_column_bands` → `_table_markup`, using the new `guides.table_columns` — **NOT `guides.py`'s reflow, which is what this row, STATUS.md and F167 all said, and is why the fix failed to land twice**. `BLOCKER-PLAN.md` C9 named `emit.py` and was right | **done for the rate binding; F170 and F183 remain** | F127, F167, F168, F169 all `fixed` at r19 on the measurement above; **F170 stays open** (0605's ATC region is still cut into two tables, so its 3-line header section cannot reach `MIN_COLUMN_SUPPORT`) and **F183 is newly filed** (2551M's left `Tax Rate` label is set at x 237.60 against its own column edge of 251.52, so the label — not any rate — falls one cell left). The old grid came from `_coverage_gutters`, which calls a 1pt bin a gutter below 12% of peak; on 2551M p2 the real gutter sits at 4–5 runs against a peak of 18, so all four missing boundaries were bins the histogram called occupied. `guides.table_columns` asks where a *cell starts* instead and keeps a column only where two lines agree |
| **G14** | A BIR-only control field is emitted as a taxpayer input | 1 confirmed (2026-08-07) → 0 open | `lattice.py` field classification | **done** | F147 `fixed` |
| **G16** | **`audit.py`'s `comb_slots_match_printed` requires a comb's input indexes to run 0..N−1 with no gap, so it fails on every compartment G11 correctly refuses.** The emission contract changed; the assertion that owns it was not told | **Live 2026-08-18, batch `ddac6058`: 9 forms / 288 offenders** (0605, 1600wp, 1604cf, 1604f, 1800, 2200c, 2550m, 2551m, 2553). Not a Stage 3 gate. STATUS Z1 still records the reviewed-topology remainder. | `audit.py` `check_comb_slots_match_printed` | open | **The assertion must not be weakened.** Residual is reviewed topology / G16, not “year boxes as one input.” |
| **G17** | **A reviewed emitter pin lives in a place no one has enumerated, and only the referee reads it — so it costs a full 60-minute gate run to discover.** `comb_referee.HTML_RUNTIME_SCRIPT_SHA256` is a third such pin, distinct from `EXPECTED_HTML_STRUCTURE_SHA256` and from the four producer SHAs | 5 forms UNEVALUABLE at r14, report partial 40/53; 2 of the pin's 3 hashes had moved | `comb_referee.py:535`, read at `comb_referee.py:2822` | open | The pin itself is re-pinned and **r15 confirms it** (all five emission-binding errors gone). The class stays open because the underlying defect is the enumeration, not this pin: the "census pins that must move together" list under **How we work** did not contain it, and does not name whatever else is like it. An inventory that a producer change can be checked against in seconds — rather than at the end of an hour — is the actual fix |
| **G18** | **A human-reviewed referee control no longer holds, and it is the last thing between the referee and a complete corpus report.** `REVIEWED_2551Q_EXPLICIT_COMPARTMENTS` reviewed 2551Q `p2c5` as `measured` with 14 compartments and `p2c80` as 12; the referee now returns `unevaluable — source topology does not occupy a strict majority of the full comb band` for both | **52/53 forms report at r19** (r18: 40/53). 2551Q is the only one that errors, and it takes 105 subjects with it, which is also why `combs_found` is 4,433 against an expected 4,538. p2c5 measures 6.96pt of a 17.70pt band; p2c80 7.44pt of 18.78pt | `comb_referee.validate_2551q_referee_golden` vs whatever moved the majority rule under it | open | **The pin was NOT moved and must not be** — moving a reviewed control to match the producer that stopped satisfying it is the failure this project already paid for at `EXPECTED_COMBS` (r14) and `HTML_RUNTIME_SCRIPT_SHA256` (G17). Not caused by r19: 2551Q's `index.html`, layout and IR are byte-identical and only three *guide* documents changed; r19 merely made 2551Q reach the check. Same shape as G10's assertions — newly visible, not newly broken. **Reaching PASS is further off than this one form**: `forms_ok` is 0 and 4,385 of 4,433 subjects are `source_unevaluable`, so 53/53 would buy a complete report, not a score |
| **G19** | **A comb slot boundary is taken from a divider the page's own `comb_divider_final_visible_ids` excludes, so a money box claims a compartment the paper does not print.** The lattice already computes the right answer and records it beside the wrong one; `legacy-continuity` outranks it | 5 cells on 2550M (r20), plus the 2 on 1707/1707A that pre-date r20 — the `layout-printed-mismatch` half of `comb_slots_match_printed`, 2 → 7 | `lattice.py` comb band, `legacy_dividers` / `frame_dividers` vs `dividers` | open | **F184.** 2550M `p1c89` is the MM box of a Schedule row: the source strokes ticks at x 260.40 and 263.52, then paints a white fill over the whole box (seqno 477) AFTER the 263.52 tick (seqno 419), so one tick survives to the paper. `slot_x` is `[246.96, 260.40, 263.52, 273.84]` — a 3.12pt compartment. The cell's own `comb.resolution` reads `final_visible_candidate_cells: 2`, `[final-visible-count-regression, legacy-continuity-only]`, so it is already `active_unresolved` and already blocks the gate. **Not patched at r20 on purpose**: dropping a legacy topology is the reviewed `retired_proven_false` transition, which needs independent evidence and a human |
| **G20** | **A retained comb subject whose legacy comb RESOLVED is refused by the referee's retained-subject contract, so a whole form produces no report.** The contract encodes "retained because the topology could not be resolved"; a caption-block refutation is a third shape — resolved geometry, refuted semantics | 6 subjects on 3 forms (2200A `p1c94`/`p1c115`, 2200C `p1c84`/`p1c105`, 2200P `p1c93`/`p1c114`); the other 5 refuted subjects carry `unresolved` and are accepted. Referee 46/53 → **50/53** once the census half moved | `comb_referee.py:4133` retained-subject validation | open | **F191.** The census half (`EXPECTED_RETAINED_SUBJECTS_BY_SLUG` 22 → 33 on seven slugs) is a pin this integration owns and moved with its cause. The contract half was deliberately not touched: changing the adjudicator in the same increment as the producer it adjudicates is what cost `EXPECTED_COMBS` (r14) and `HTML_RUNTIME_SCRIPT_SHA256` (G17), and a producer rewriting its own `resolution_status` to satisfy its referee is the same fault mirrored |
| **G21** | **A caption printed beside a comb is swallowed into the comb's FIRST compartment, which then carries a live single-character input laid over the printed words.** The comb's other compartments are correct money boxes, so the caption-block refutation deliberately leaves it alone | 3 cells, 3 forms: 2200A `p1c111`, 2200C `p1c101`, 2200P `p1c110` — slot 0 is 173.66pt wide against a 14.55pt pitch for slots 1-28 | `lattice.py` cell segmentation / run assignment | open | **F190.** `comb_compartment_glyph_counts` for the cell is `[17, 0, 0, … 0]`: SOME compartment is multi-glyph and EVERY compartment is not, which is exactly why `printed_caption_refutes_comb` is stated over every compartment — refusing here would have cost 28 real money boxes on three forms |
| **G15** | **The `?debug=fields` overlay shipped in `forms/` is the OLD self-referential one; the fixed overlay exists only in `emit.py` and has never been regenerated into the corpus.** In the shipped legend blue dashed means "this input is fine"; in the fixed one it means "printed box with NO input" — the inverse | was 0 / 38; **now `printed box with no input` → 53 of 53, `no usable box` → 0** (r14, 2026-08-07) | `emit.py` overlay, unregenerated | **done** | F172 `fixed`. Nothing needed fixing — the corrected overlay already existed in `emit.py` and had simply never been written out. Regenerating the corpus at r14 shipped it. The Stage-1 definition of done is no longer blocked on the overlay |

G10 is the one to read twice. It is why Stage 2's central guarantee is not yet
real (see Risk R1). **G11 was fixed first** and is `done`: it was the only row
where a single producer bug put a live text box on a statutory constant. Its
successor is **G16**, which is that fix's unpaid half — the assertion that owns
the emission contract was not told the contract changed.

**Not measured / not yet diagnosed:**
- ~~How many of the 626 both-endpoints-unsupported borders have a round or
  projecting cap.~~ **MEASURED at r20: 625, not 626, and 98 of them (15.7%)
  carry a round or projecting cap; 527 are butt-capped, where modelling the cap
  changes nothing by construction.** Stroke census behind it: of 569 OPEN
  stroked subpaths, 229 butt / 270 round / 70 projecting; every open subpath in
  the corpus is a single `l` op, and the 133 multi-`l`-op paths are all CLOSED
  rectangles, which must not be capped.
- Why neither failing assertion moved when the painted-wall fix widened 131
  cells and created 95. **NOT DIAGNOSED.** A number that does not move when it
  should deserves the same suspicion as one that moves wrongly.
- Whether `ddce158`'s referee claim reproduces. **NOT MEASURED.**

---

## Stage 2 — correct

> **Reconciled 2026-08-08 (user decision): batch-versioned immutability.**
> Stage-1 batches are immutable once a sighted gate scores them (tag
> `corpus/rN` per verdict); a generator fix produces the NEXT batch, never a
> mutation of the last. Stage 2 builds `forms-corrected/` from a NAMED batch:
> byte-copy per form, then apply that form's correction records — no record
> means byte-identical copy. The applier's manifest names source batch, every
> record, and input/output sha256. The gate runs on BOTH trees; on the
> corrected tree fidelity must fail ONLY at declared divergences, each named.
> Stage 3 binds to `forms-corrected/` only. Full rationale and the
> counter-check that amended the never-regenerate clause:
> ARCHITECTURE.md § Batch-versioned immutability.


Not built. Four binding rules, not open for relitigation (ARCHITECTURE.md
§"Rules the user set"; rule 4 is the design constraint they imply):

1. **A correction never hides a divergence.** The fidelity check still compares
   against the official PDF and still **FAILS** on a corrected field, reporting
   `diverges by declared override <id>, authorised by <authority>`.
2. **Fix the generator; override only the residue** — a short reviewable list,
   never a parallel corpus.
3. **Every correction declares its EXPECTED EFFECT** and a verifier re-derives
   it from the corrected output. A correction that cannot state its effect in
   advance cannot land.
4. **The verifier must not share a producer with the correction.** Re-derive
   from `pdftocairo -svg` or the re-extracted print-to-PDF IR — never from the
   `build/layout/*.json` the correction just mutated. This is the
   `?debug=fields` failure (233/233 OK on a visibly wrong page) and the
   `save()`/`verify()` failure (`3bf32c8`) restated as design.

**Correction record — minimum fields:** `id`, `form`, `subject` (cell/field
identity), `what` (the change), `reason`, `authority` (regulation or release
note, citable), `expected_effect` (machine-checkable), `verified_by` (the
independent producer that re-derives it).

### The register — one entry

| ID | Form(s) | Change | Authority | Expected effect | Status |
| --- | --- | --- | --- | --- | --- |
| C01 | all TIN combs | branch code 3 digits → 5: `000-000-000-000` → `000-000-000-00000` | in-repo: `frm2550m:txtBranchCode` carries `max_length: 5` sourced from `official-hta-runtime#control:L409` | the TIN comb's trailing group emits 5 slots, not 3; total TIN slots 12 → 14 | not built |

Why C01 is genuinely stage 2: the 2007 PDF is correct **and** out of date. No
rule derives "BIR widened this in 2018" from 2007 artwork. Its filing-safety
rationale is independent of the artwork — the HTA runtime the real eBIRForms
client ships declares the width.

Nothing else belongs here yet. Anything proposed for this table must first be
shown *not* to be a stage-1 row above.

---

## Stage 3 — map

**R1 TIN mapper is `map_tin.py`.** The three preconditions hold. Only the
163 unique harvested TIN keys are copied onto input `name=`, and only on
`forms-corrected/`. R3/R7 stay classified, not joined. Comb-referee PASS,
G10/G16/G17, and C01–C07 `verified` are not this program.

The identity catalog is 9990 records, 9990/9990 on `forms/` and
`forms-corrected/` (`tools/formgen/identity/`). That is the freeze R2
asked for, not the mapper. The HTML cell id is a hint; a unique overlap
with a different `p1cN` is `html_id_hint_stale` and must update the
catalog in the same commit.

The naming problem is already solved on BIR's side: `rules/forms/*/fields.json`
carries 43 forms and 9,592 field names harvested from the official HTA runtime,
with `serialized_key` values like `frm2550m:txtBranchCode`.

The join is also far from bijective (measured 2026-08-06):

| Gap | Measure |
| --- | --- |
| Bundles with no `fields.json` at all | 13 of 53 |
| Joinable codes with revision skew | 8 |
| Official fields with `serialized_key: null` | 1,234 of 9,592 |
| 0605: names we emit vs official fields | 71 vs 235 |

**Preconditions before stage 3 opens (all three hold; mapper still closed):**
1. **Holds.** Field identity is a catalog id, not a bbox and not `p1cN`.
   9990/9990 fillable cells resolve `exactly-one` against `forms/` and
   `forms-corrected/`. `EXPECTED_UNCATALOGUED_FILLABLES = 0` is pinned in
   `field_identity.py` and `gate.py`.
2. **Holds.** Stage-1 rows G02, G03, G04 (and G02a–i, G12, G14) are `done`.
   Live 2026-08-18, batch `ddac6058`: `inputs_span_no_printed_divider` 0/53,
   `printed_box_peers_all_fillable` 0/53. G16 remains open (9 forms / 288)
   and is not a Stage 3 gate.
3. **Holds.** `field_identity.py ledger-check` is green on open, fixed, and
   not-a-defect findings (259/259 cited cells on `forms/`). Fillable
   subjects cite catalog ids; live labels keep a current cell id; vanished
   cells are `former_pXcN`.

---

## How we work

Process rules earned the hard way. Each one cost a day or a 60-minute gate run.

- **Regenerate and commit generated files before a gate run.** A stale generated
  file now fails in 5 seconds (`2bd1c2d`) instead of 50 — but it still fails.
- **One agent per file.** Two agents on `emit.py` once cost a day.
- **A schema change is declared everywhere it is asserted, in the same commit** —
  `gate.py` `BATCH_RECORD_KEYS`, the gate's self-test fixtures, the census pins.
  G01 exists because this was not done.
- **Census pins that must move together:** `gate.py:72-80`
  (`EXPECTED_FORMS`, `EXPECTED_IN_CORPUS_FORMS`, `EXPECTED_EXTRA_FORMS`,
  `EXPECTED_COMB_SUBJECTS`), `comb_referee.py` (`EXPECTED_FORMS`,
  `EXPECTED_COMBS`, `EXPECTED_COMBS_BY_SLUG`, `EXPECTED_HTML_STRUCTURE_SHA256`,
  **`HTML_RUNTIME_SCRIPT_SHA256`**, and the four producer SHAs
  `LATTICE_/AUDIT_/EXTRACT_/VERIFY_PRODUCER_SHA256`), and **`guides.py`'s
  per-page expectation table**. The last two were added at r14 after each cost a
  run: `guides.py`'s at self-test time, `HTML_RUNTIME_SCRIPT_SHA256` at the end
  of a 60-minute gate, because only the referee reads it and the referee runs
  last. **This list has been wrong every time it has been consulted — treat it
  as a starting point, not an inventory. That is G17.**
- **Adding an assertion touches four places, not one** (added r18, and it is
  now self-enforcing): `audit.ASSERTION_KEYS` + `audit.CHECKS`;
  `gate.REQUIRED_ASSERTIONS`; `gate.BASIC_ASSERTION_COUNT_FIELDS` (an exact
  allowlist — an undeclared published count field reads as
  `detail has unsupported fields`); and the `basic_counts` block of
  `gate._synthetic_audit_record`, which every gate self-test fixture is built
  from. `gate.self_test` now asserts that every non-comb name in
  `REQUIRED_ASSERTIONS` has a declared count contract, so omitting the third
  step fails in 3 seconds instead of at the end of an hour. `comb_referee`'s
  `AUDIT_PRODUCER_SHA256` re-pins with it.
- **`gate.py` does not allowlist assertion-detail SHAPES for the basic
  assertions, only names and count-field names.** The `broken`/`held` contract
  (`holds`, `reason`, `offenders`, `offender_count`, `offenders_published`,
  `offenders_omitted`, `offenders_complete`) is validated structurally and needs
  no change for a new assertion that uses it. Only
  `_normalise_outer_comb_assertion` / `_normalise_outer_offender` are
  shape-exact, and they apply to `comb_slots_match_printed` alone.
- **A check that cannot be evaluated is a FAILURE**, never a pass. UNEVALUABLE
  is a red verdict.
- **Determinism cannot certify a correction applier** — it runs the writer twice
  and both halves drift together (`3bf32c8`).
- **Never edit a check to make it pass.** Never weaken a tolerance
  (position 0.25pt, thickness 0.05pt, advance 0.10pt, size 0.01pt).
- **A finding resolves in the ledger, with evidence, in the same commit as its
  fix.**
- **Any commit that moves a number updates STATUS.md in the same commit.**

---

## Definition of done — as commands, not adjectives

**Stage 1**

```sh
python3 tools/formgen/gate.py                      # exits 0 — all 12 checks, no UNEVALUABLE
python3 -c "import json;d=json.load(open('tools/formgen/review-findings.json'));\
print(sum(1 for f in d['findings'] if f['status']=='open' and f['severity'] in ('blocker','major')))"
                                                   # prints 0
gh pr checks 13                                    # every check green
```
Plus: the user reviews the rendered forms through a **fixed** `?debug=fields`
overlay — one that measures against a producer other than the one that emitted
the boxes.

**Stage 2**

```sh
python3 tools/formgen/gate.py                      # still exits 0 WITH corrections applied
# and the fidelity report names every override:
grep -c 'diverges by declared override' build/audit.json   # equals the correction count
```
A correction whose declared `expected_effect` is not independently re-derived
is a failed correction, not a pending one.

**Stage 3**

```sh
# every emitted input name joins to an official serialized_key, or is listed as
# deliberately unmapped with a reason:
python3 tools/formgen/<mapper>.py --check          # 0 unjoined, 0 unexplained
```

---

## Blocked — needs the user

Nothing.

**Retracted 2026-08-07:** this section previously claimed CI was dead across the
repository, probably from exhausted Actions minutes. That was WRONG on both
counts and is corrected here rather than deleted, because the reasoning failed
in an instructive way. The repository is PUBLIC, so GitHub-hosted minutes are
unlimited and quota could never have been the cause. And CI had in fact run on
this branch at 23:05 -- `CI` passed, `formgen` failed. I ran
`gh run list --branch` once, got an empty result, and concluded "no runs
repo-wide" from a single negative observation without checking whether the
repository even had a quota to exhaust. The real failure was ours:
validate_tree's markup scan reading a `<image>` inside a JavaScript comment.

## Risk register

Condensed to what changes behaviour.

| ID | Risk | Consequence | Mitigation |
| --- | --- | --- | --- |
| **R1** | **Stage 2's guarantee is close to vacuous today.** The check that is supposed to fail on an override is blind to the field layer: 137/138 findings are `audit_blind: true`; blocker F028 (live inputs over 1700's statutory tax brackets) sat on a form scoring rules 100% / text 100% / 0 missing / 0 extra. | "A correction never hides a divergence" certifies nothing. | Each override must **name the specific check that fails on it and prove it fails**. Close G10 before Stage 2 ships. |
| **R2** | Field identity was a quantised bbox; every geometry fix renumbers ids. | Ledger and mapping both drift silently. 42/146 cited ids already dead. | Catalog ids (`2550m-2007/p1/tin-branch`) plus center-in-printed-box match. `p1cN` is a hint. TIN caption-chain class catalogued 2026-08-18 (180 identities, 44/53 bundles); not yet coverage of every fillable field. Treat a stale hint as a schema change. |
| **R3** | A checker sharing an assumption, code path or source of truth with its subject — 11 instances found so far. | The largest instance would be a self-verifying correction system sitting between the generator and everything downstream. | Rule 4 above: independent producer, always. |
| **R4** | Census pins drift apart (G01, live now). | 60-minute gate run fails on its own constants. | The pins-move-together list under "How we work". |
| **R5** | The comb-referee's 53 reviewed HTML hashes invalidate on **every** legitimate producer change. | Either maximum conservatism or unworkable friction. | Open design question in GOAL.md §Blocked: hash the tag/attribute skeleton, not every byte. **Undecided.** |
| **R6** | Stage-1 fixes that only move a number, not the defect — the painted-wall fix widened 131 cells and moved neither assertion. | Effort spent with no verified effect. | Every fix declares its expected effect too, not only corrections. |
| **R7** | 8+ open findings are TIN-class severity (unenterable Fiscal / Amended / quarter checkboxes, unenterable money boxes). | A form that cannot be filled is as unsubmittable as one filled wrongly. | G03 is not a "minor" row; it is the same class of harm as C01. |

---

## Implementation packages — r37+ (diagnosed 2026-08-10 at `5cd4017`, main agent)

Written so an implementing agent can execute each package mechanically. Every
fact below was **measured on this tree**, not assumed. Baseline: gate r36
**10/13**; `inputs_over_printed_text` 6 forms/15; `comb_slots_match_printed`
12 forms/25; `inputs_span_no_printed_divider` HOLDS 0; findings 18/133 open
blocker+major; comb-referee 33 (the retained floor); determinism
`56248287ed77`; 45,485 inputs; `EXPECTED_COMB_SUBJECTS` 4583.

Execute **P1 → P3 → P2 → P4** (P3 before P2 is deliberate: P3 removes the
underscore cells from P2's population). One package per round. Division of
labour: the implementing agent does implementation + self-tests + scratch-copy
mutations + regeneration + measurement + pins; the operator (main agent) does
the ledger closures, the full gate, commit and push.

Standing rules, restated because they outrank finishing: never widen a
tolerance or weaken an assertion; never special-case on form code or slug; a
check that cannot be evaluated is a FAILURE; `audit.py` is the judge for
P1/P2 and is **locked** there; every census pin moves in the same commit with
the cause named; `batch.py` does NOT refresh `build/audit.json` — run
`audit.py --assertions-only` separately and measure on the tree actually
written; report real numbers including the ones that got worse.

### P1 — knockout-bitten walls (closes F097, blocker) — READY

**Defect.** 2200C p1 item 1 "Date (MM/DD/YYYY)": only DD is typeable. The
frame's rails carry a 1.56pt white bite mid-height; the cell walk leaks
through the hole; MM and YYYY dissolve into blank slivers instead of comb
cells (p1c122 leaks out to x=219.72).

**Measured mechanism** (`build/ir/2200c-2018.ir.json` p1):

- Solid full-height walls (th .48, y 115.22–132.14) at x0 = 59.52, 73.94,
  102.98, 117.38. DD (p1c5) sits between two of them → comb 2, works.
- The rails at x0 = 30.60 and 175.34 are each THREE collinear fragments:
  black 115.22–124.94, **white (gray=1.0) 124.94–126.50**, black
  126.50–132.14. Both black pieces still reach the frame's top and bottom
  rules — the bite is strictly interior to one drawn stroke.
- Short bottom ticks (th .24, y 125.42–132.14) at x0 = 45.12 (MM), 88.58
  (DD), 132.02 / 146.54 / 160.94 (YYYY).

**Corpus census of the signature** — a collinear same-axis knockout STRICTLY
covering a sub-writable gap between two black fragments of one cluster —
is exactly **8 bites in 2 forms**, nothing else in the corpus:

- `2200c-2018` p1, axis v, gap y 124.94–126.50 (1.56pt) at line positions
  x ≈ 30.84, 175.58, 189.98, 334.75, 450.55, 508.54, 537.46 (the row-wide
  white band bites every rail it crosses on the top row).
- `2000-dst-2018` p1, axis v, x ≈ 192.38, gap y 120.62–122.18 (1.56pt);
  same three-fragment signature (v-rule x 192.14–192.62).

**Three negative cases that MUST stay negative** (all measured; encode each
as a fixture):

1. **A perpendicular witness is a junction statement — never bridge.**
   2200A p1 x0=580.66: black y 136.94–146.30 and 146.78–153.02, gap 0.48pt;
   the only white is the PERPENDICULAR h-rule y0=146.30 (x 537.70–594.60).
   The sheet severed the column from the rule above to make it a comb
   divider (p1c24, divider_x [551.86, 566.38, 580.90]); bridging would split
   the 4-slot comb. The same-axis + collinear condition excludes it.
2. **A witness that abuts the gap does not cover it — never bridge.**
   1800-2018 p1, line y≈805.46: black h-segments end exactly where
   full-height columns cross (gaps 0.01–0.24pt at x 194.92, 290.33, 317.69,
   345.07) and the white segments (y 805.54–805.78) also SKIP those ranges,
   ENDING at the gap edge. With ±CLUSTER_TOL_PT slack a 0.24pt gap is
   swallowed by a witness that merely touches it, so coverage must be
   STRICT: `k[al0] <= a1 + 1e-6 and k[al1] >= b0 - 1e-6`. Same for
   1604e-2018 p1 y≈383.6 (a 0.01pt thick/thin butt-joint notch).
3. **A doorway is a real passage — never bridge.** Bound the gap by the
   form's own `min_fillable_line_metrics(ir)["glyph_height_pt"]` (2.930pt on
   the smallest form; both real bites are 1.56pt). Metrics absent → bound
   0.0 → never bridge.

**Change** (`tools/formgen/lattice.py` only):

New pure helper near `build_lattice` (:1881):

    def bridge_knockout_bites(lattice: Lattice,
                              knockouts: Sequence[dict[str, Any]],
                              axis: str, max_gap_pt: float) -> int

- Returns the bridge count (tests and the probe use it); mutates
  `lattice.spans` in place. `if max_gap_pt <= 0.0 or not knockouts: return 0`.
- Along keys `("y0","y1") if axis == "v" else ("x0","x1")`.
- Per line i: `local = [k for k in knockouts if abs(centre(k) -
  lattice.positions[i]) <= CLUSTER_TOL_PT]`; need ≥2 spans and a local
  witness.
- Consecutive spans (…,a1),(b0,…), `gap = b0 - a1`: bridge iff
  `0 < gap < max_gap_pt` and a local witness covers it STRICTLY (epsilon
  1e-6 — see negative case 2). Merge intervals, count.
- Do NOT compare witness thickness: `line_thickness_gray` reports the
  page-wide cluster max (2000-DST's line reports 0.96 while its local
  fragments are 0.48), so a thickness test would misfire.
- Docstring carries the bite / junction / doorway trichotomy with the three
  measured cases above, and why strictness and same-axis are load-bearing.

Caller — `build_page`, immediately after the two `build_lattice` calls
(:6220–6223), before `merge_grid` (:6234):

    knockout_v = [r for r in page["rules"] if r.get("axis") == "v"
                  and tone_role(r.get("gray")) == "knockout"]
    knockout_h = [r for r in page["rules"] if r.get("axis") == "h"
                  and tone_role(r.get("gray")) == "knockout"]
    bite_bound = (0.0 if fillable_metrics is None
                  else float(fillable_metrics["glyph_height_pt"]))
    bridge_knockout_bites(xl, knockout_v, "v", bite_bound)
    bridge_knockout_bites(yl, knockout_h, "h", bite_bound)

Do NOT touch the raw/legacy lattices (:6044–6046): the legacy view keeps the
old reading; the new comb subjects must register as NEW ACTIVE subjects
through the existing ledger flow.

**Fixtures + mutations** (in `self_test`, same style as the shading-seam
block ~:6790): positive bridge (two collinear black v-fragments, 1.5pt gap,
exact white fragment, bound 3.0 → count 1 and `covers()` true across the
joint band); bare-paper gap → 0; the 2200A shape (perpendicular white only)
→ 0; the 1800 shape (white abutting the gap edge) → 0; doorway (gap 5.0,
bound 3.0) → 0; bound 0.0 → 0. Scratch-copy mutations, each tripping exactly
its own check: strict→±CLUSTER_TOL_PT (abut check fires), drop the
same-axis/collinear filter (perpendicular check fires), drop the size bound
(doorway fires), bridge without witness (bare-paper fires).

**Verify in this order — expected numbers are acceptance, deviations are
STOP-and-report:**

1. `python3 tools/formgen/lattice.py --self-test --ir
   build/ir/2551q-2018.ir.json` → PASS.
2. Probe (scratchpad script): monkey-wrap `bridge_knockout_bites` to record
   counts, run the module's own page build over all 53 IRs → **v-bridges 8
   (7 on 2200c-2018 p1, 1 on 2000-dst-2018 p1), h-bridges 0, all other
   forms 0.**
3. `python3 tools/formgen/batch.py --report build/batch-report.json`;
   `git status` — shipped bytes may change ONLY for the two forms (plus
   their provenance and forms/index.html). Any other bundle → STOP.
4. Layout deltas, enumerated per cell in the report: 2200C p1 gains a
   2-slot comb at x≈30.84–59.76 (divider ≈45.24) and a 4-slot comb at
   x≈117.62–175.58 (dividers ≈132.14/146.66/161.06); the whole top row
   re-forms and the page's cell ids renumber. 2000-DST p1: the wall at
   x≈192.38 restores; the cell spanning it splits.
5. Fresh judge: `audit.py --assertions-only` →
   `comb_slots_match_printed` holds or improves from 12/25 and the two new
   combs land in agreement (printed 2/4 = latticed = emitted); any NEW
   offender is diagnosed in the report before commit.
   `inputs_over_printed_text` ≤ 6/15; the two zero families stay 0.
6. Census + pins, one commit: gate `EXPECTED_COMB_SUBJECTS` 4583→4585;
   referee `EXPECTED_COMBS` 4583→4585, `EXPECTED_COMBS_BY_SLUG["2200c-2018"]`
   +2; `EXPECTED_HTML_STRUCTURE_SHA256` recomputed for exactly the two
   slugs; `LATTICE_PRODUCER_SHA256` re-pinned with a dated cause. If the
   re-formed row creates combs beyond +2, list each with its ticks and move
   the census by the true, named delta.
7. Referee corpus run (CLI per gate `_comb_referee_command`): forms_error 0;
   combs_found = new census; both new subjects `measured` (a
   source-unevaluable landing is reported with its reason before shipping);
   emission mismatches stay 33; `subjects_retained_unresolved` stays 33;
   pending_transitions 0. A retained subject changing state → STOP.
8. `guides.py --self-test` — if the per-page tables for 2200C p1 /
   2000-DST p1 move, update those pins with the per-cell cause (precedent:
   commit 9f76779).
9. Input delta vs HEAD, counted the same way on both sides: expect +6 on
   2200C (2+4), small ± on 2000-DST; report exact.
10. Hand back to the operator: F097 closure text (mechanism, bridge
    conditions, shipped slot counts, census moves, the three negative cases
    proven by fixture), full gate (expect 10/13, findings 18→17), commit,
    push.

**P1 landed (2026-08-10, gate r37 pending).** Five measured deviations, none
tuned away: the healed boxes became region FIELDS, not combs (+0 comb census
-- comb subjects arise only through the legacy-lattice discovery flow, which
P1 deliberately did not bridge); 2200C gained a third writable box (p1c117,
+2 inputs, total +8); 2000-DST's formerly-agreeing 6-slot comb split into 2+4
regions and its subject went retained_unresolved (blocks_gate), moving the
retained census 33 -> 34 openly; pending_transitions was never 0 at HEAD
(118) so that target was wrong in this plan. All recorded as **F201**.
**P1b (design question, do not implement without the operator):** whether
`bridge_knockout_bites` belongs in the LEGACY lattice too, so bridge-healed
boxes can register comb subjects -- it changes what "the old reading" means
for a wall the source always drew, and it is the only route to comb-model
inputs for these cells and to retiring 2000-DST's suppressed subject through
a reviewed transition.

### P3 — ruled blanks, fixed upstream (closes F148, F149, F200) — before P2

The reverted attempt (commit `5cd4017`, finding F200) proved the emit-side
fix collides with `inputs_over_printed_text` while underscores are TEXT: an
input on the ruled blank necessarily overlaps the run that draws it. Fix it
upstream: `extract.py` reclassifies an underscore group as the RULE it
typographically is. The blank becomes paper under a drawn rule; the lattice's
new h-line at the blank's baseline splits the caption cell from the blank
strip; the strip becomes an ordinary field cell and receives its input
through the NORMAL flow. No assertion needs an exception anywhere.

**All three prerequisites were answered by the operator on 2026-08-10 at
`871d880`, on the r37 tree. They are measured facts, not assumptions — do not
re-derive them, but DO re-measure the census after your change.**

1. **The upstream route works — this was the STOP condition and it is
   clear.** `Bundle.ink` (audit.py:6731-6741) builds the index from
   `self.pages[page]["text_runs"]`, filtered to the runs the document emits,
   through `glyph_boxes(run)`. It does NOT use `drawn_glyph_boxes` /
   `page.get_texttrace()` — that is `SourceSlotOracle`'s separate oracle. So
   a group reclassified out of `text_runs` leaves `emitted_runs`, leaves the
   ink index, and `inputs_over_printed_text` cannot see it. No exception is
   needed in any assertion.
2. **GROUP census, measured — this is the number your implementation must
   reproduce.** A group = ≥3 consecutive `_` glyphs inside one run, split at
   any other glyph. Corpus: **114 runs carry ≥1 group, 119 groups, 50 cells,
   23 forms, and all 50 cells are `kind == "label"`.** By form (groups):
   1600-pt-2018 20, 1600-vt-2018 20, 1702mx-2018c 14, 1706-2018 14,
   1606-2018 9, 2550q-2024 5, 1700-2018 4, 1801-2018 4, 2200m-2018 4,
   1701q-2018 3, 2200a-2020 3, 2200t-2022 3, 1600wp-2010 2, 1601c-2018 2,
   1701ms-2024 2, 2200an-2018 2, 2200p-2020 2, and 1 each on 1603q-2018,
   1701-2018, 1701a-2018, 1707-2021, 2550-ds-2025, 2551q-2018.
   Contrast the WHOLE-RUN census (52 runs / 34 cells / 13 forms) that the
   reverted attempt validated against while shipping group matches: 1600-PT
   and 1600-VT alone carry 20 groups each and appear in neither earlier
   list. Reconciling these two numbers is the whole point of this step.
3. **No schema lock on rule dicts.** No key allowlist over IR rules exists in
   gate.py / validate_tree.py / comb_referee.py; `_BATCH_MOVED_COUNTS` is a
   batch-report count set, not a rule schema. A rule already carries 14 keys
   (`axis, gray, id, length_pt, paint_seq, paint_seq_max, paint_spans, rgb,
   role, thickness_pt, x0, x1, y0, y1`), so adding provenance is open. The
   real constraint is the `rules_missing` / `rules_extra` parity the `rules`
   check scores: it moves SYMMETRICALLY here — the source IR gains the
   strokes, emit draws them, the round trip re-extracts them — which is why
   this route is expected to keep `rules` clean on 53/53. Verify that; do not
   assume it.

Change (`tools/formgen/extract.py`): split text runs at underscore-group
boundaries; publish each group as an h rule at the GLYPH'S OWN INK BAND,
measured from the extraction API's per-glyph boxes (rawdict/texttrace) — if
the band is not derivable for a group, LEAVE IT AS TEXT and count it (fail
closed, never guess); tone from the run's fill; the group's glyphs leave the
run's text. Add extract mutations: a 2-underscore group stays text; an
underivable ink band stays text; a mixed run splits into text+rule+text at
the measured extents.

Verify: text parity stays clean 53/53 (both sides re-extract with the same
extractor — symmetric by construction); rules parity stays clean (emit draws
IR rules; the round trip re-extracts the drawn stroke); the caption cells
split and ids renumber on ~13 forms; every blank strip's classification is
reported — a strip the sliver rule refuses (height < glyph height) gets no
input and is RECORDED, not forced; `inputs_over_printed_text` improves;
comb censuses unchanged; structure pins recomputed for every touched form;
extract self-test probe counts stay pinned. Close F148/F149 on shipped-bytes
evidence, mark F200 fixed ("upstream reclassification — option 2 of the
recorded pair"), and re-verify the full census population end-to-end.

**P3 landed (2026-08-10, gate r38 pending).** The upstream reclassification
works and F200 is closed: 118 of 119 groups are now rules, the 1 refusal stays
text and is counted, and rules/text parity held clean 53/53 on a full
round-trip audit. But **F148/F149 are NOT closed**, for a geometric reason
that reshapes P2: **a ruled blank is written ON TOP of its line, not below
it.** The rule lands at the glyphs' descender band -- the bottom of the
caption's line box -- so the split returns the writing space to the CAPTION
cell and leaves a sliver below (1701 p4: caption 11.54pt, strip 3.22pt
`blank`). Corpus-wide 3 of 47 split strips are `field`; +1 input total.
**P2 therefore absorbs this**: its target is now BOTH the row-number
description cells (F151) AND making the space above a published ruled-blank
rule writable inside its caption cell (F148/F149). Both are the same question
-- a cell that is part printed constant and part writing surface -- and the
ruled-blank case now has a source-published rule marking exactly where the
writing surface begins, which the F151 case does not. Census deviation to
reconcile in P2: the group census attributed **57** label cells, not the 50
the operator measured; all 57 are `label` with 0 inputs and the agent could
find no filter yielding 50.

### P2 — part-constant description rows (F151, blocker) — measure first, abort honestly

AFTER P3, so the underscore cells are already out of this population.

Target: 1701-2018-conso p2 Schedule D p2c132/136/140/144 (x 26.16,
w 452.71) and Schedule C p2c97/103/109 (x 54.24, w 283.61) — each kind
`label` holding ONLY a row number (`1 `, `2 `…), ≥97% blank, bordered, with
fillable siblings. The measured trap: 1,875 label cells carry a blank run
≥100pt, most being section headers and caption rows that must NEVER gain an
input; bordered item-number boxes are labels whose ink fills them.

Measurement script first, on the post-P3 tree, for every label cell:
border_count; shading coverage AT THE CELL (`on_shaded_paper` with the
form's glyph height — label cells were never asked); printed-ink x-extent as
a fraction of width; whether the ink is a single leading cluster; the blank
remainder's width × height against the form's own line metrics. Anchors =
the 7 target cells; counter-anchors = full-width unshaded caption rows and
item-number boxes. Encode a rule ONLY if the corpus separates bimodally with
a constant-free bound (precedent: the 4.4× separation behind
printed_partitions, log r33). If the populations overlap → ABORT: publish
the distributions, leave F151 open with the measurement attached.

If separation holds: extend the classification (lattice `classify_cell` or
emit `field_verdict` — pick the layer that keeps audit.py independent; it
stays locked) so a bordered, unshaded cell whose printed ink is a leading
constant with a viable blank remainder is a FIELD; emit already trims the
writing box past leading ink. Every cell that gains an input corpus-wide is
listed in the report; `inputs_over_printed_text` must not regress.

**P2 MEASURED AND HALF-ABORTED (2026-08-10, operator, on the r38 tree).** The
measurement ran first as the package demands, and F151's seven cells proved to
be two populations:

- **Row-number cells — SEPARATES, not implemented.** Label cells sharing a row
  with >=1 field cell and holding only a short numeral: 296 corpus-wide.
  Bounding the trailing blank by the form's own `line_width_pt` (the sliver
  rule's existing metric, no new constant) and requiring the cell to be tall
  enough to write in gives **296 -> 56 cells across 23 forms**, catching all
  four Schedule D rows (452.7pt, blank >=2x) and excluding all 188 narrow
  item-number boxes (14.8pt holding "12"). Distribution: 188 <0.5x, 52 >=0.5x,
  11 >=1.0x, 45 >=2.0x.
- **Zero-ink cells — DOES NOT SEPARATE, aborted.** 419 label cells contain no
  text at all; their assigned run lies outside their box. All 419 pass the
  blank/height test by construction, so 419 -> 419. F151's three Schedule C
  rows are in here. Recorded as **F203** (major) in its own right.

**Decision surfaced, not taken:** landing the row-number rule alone gives
inputs to 56 cells across 23 forms that no finding has reviewed against the
official sheets, and F151 would STILL not close because its Schedule C half is
in the unseparable population. That trade is the user's. Do not implement it
speculatively.

Note the row-number rule would ALSO be the natural home for P3's residual
(making the space above a published ruled-blank rule writable), since the
ruled-blank case has a source-published rule marking where the writing surface
begins -- evidence the F151 case lacks entirely.

### P4 — placement/artwork family (diagnosis recipes)

- **F027/F030** (stray black bar outside the frame, 1700/1701 p1, every
  page): locate the bar in the IR, then in the SOURCE content stream; check
  the clip state (extract models clips since r20's CLIP_PROBE work). If the
  source draws it clipped away and we paint it → extract/emit clip bug; fix
  and add the shape to the clip fixtures. If the source paints it unclipped
  → faithful rendering, close not-a-defect with the operator evidence.
- **F064/F065** (1707 items 8A/9: drawn comb band / white specify line, no
  input): NOT the P1 mechanism (1707 has zero bites in the census). Probe
  the cells (x ~275–594, y ~336–347 and item 9's line): kind, comb,
  field_verdict reason, ledger state — then choose the fix.
- **F070/F102** (runs set 4–5pt too high: 1707A `Calendar`, 2200P
  ` Total Tax– `): diff IR run y against emitted CSS top for the named runs;
  the delta should implicate one emit placement path; add the run shape to
  emit's self-test with the fix.
- **F060/F120** (guide reflow: superscript reordered out of `(4th)`;
  orphaned ATC codes): both live in `emit.reflow_page` → `_column_bands` →
  `_table_markup` (~emit.py:3305). Reproduce on those two guides; fix
  ordering/row-fill; verify no other guide's bytes move.
- **F134** (2553 input over `DD` header): re-verify by GEOMETRY on the
  current tree (ids renumber). If it is the documented side-bearing
  over-reach (audit.py:541–548), close not-a-defect citing F199; if real
  ink inside a comb rectangle, it joins F199's frozen-geometry list —
  report, do not force.
- **F073** (1800 centavos): the region split landed in r33; the residual
  claim is font size/overflow. Compare the region inputs' fitted face
  against sibling money rows; fix face selection or close with
  measurements.
- **F166** (2550Q address/ZIP): locate by geometry — an earlier probe found
  p1c6 at 4 slots / 4 inputs, so it may already be fixed; verify against
  the official raster crop before closing.

**P4 landed (gate r39, 10/13). Findings 19 -> 9 open blocker+major.** Nine
findings resolved: F070/F102 shared one upstream root cause (a MuPDF rawdict
span can carry two baselines; `extract.baseline_groups` now cuts at each);
F060 fixed by proving line membership from ink rather than widening the 2.00pt
window it failed by 0.04pt; F027/F030/F134/F166/F120 closed as already-fixed on
geometry; F073 closed NOT-A-DEFECT with all three claims refuted by number.
F155 closed separately -- r35's shading-seam rule had already fixed it, exact
rect match, six cells now `shaded` with 0 inputs. F064/F065 stay open with
mechanisms recorded.

**A checker-of-checkers regression, mine, found and closed.** P3 landed three
ruled-blank checks with no source-level mutation; P4 added a fourth name.
`prove_fixtures_fail.py` runs in **CI, not the gate**, so gates r37/r38 were
green over it. Proven both ways by CI itself: `c2ca292` formgen FAILURE ->
`00e7f47` formgen SUCCESS. Fixed with four genuine mutations; CONTRACT_ONLY
untouched at 5; mutations 9 -> 13.

**GAP WORTH CLOSING: the gate does not run `prove_fixtures_fail`.** A
checker-of-checkers regression passes the gate silently, and this one was found
only because a P4 agent ran the script unprompted. Adding it to the gate's
`self-tests` check is small and would have caught this in r37.

### P5 — needs the user (do not implement)

- ~~**F154** sworn-declaration strip~~ and ~~**F156** `WE` swap claim~~ —
  **RESOLVED 2026-08-11 by the user's own visual review**, which is exactly
  the evidence P5 said they needed. Both closed `not-a-defect`; see the T
  packages below for what the review opened instead.
- The three structural blockers stay deliberately deferred and are listed
  in F199/F196 and GOAL.md: audit runtime attestation (comb-referee can
  never PASS without it), glyph ink extents (12 of the 15
  `inputs_over_printed_text` survivors are documented over-reach; a
  font-outline route via bundled Arimo/Tinos exists), and build_lattice
  fused positions (F196's 6 cells).

---

## Implementation packages — T1–T5 (from the user's visual review, 2026-08-11, at `ae19137`)

The user tab-walked 1701 (pages 1–4) and 0619E by hand with `?debug=fields`
and reported what they found. Every symptom was then traced to a mechanism
before anything was planned; the facts below are **measured on this tree**.
Baseline: gate r46 **10/13**; `inputs_over_printed_text` 3 forms/6;
`comb_slots_match_printed` 10 forms/19; comb subjects 4,587 / retained 33;
**45,333 inputs**; findings 10 open blocker+major.

Execute **T1 → T2 → T3+T4 → T5**, one package per gate round. T1 and T2 are
disjoint (`emit.py` vs a new tool) and may run concurrently; nothing else may.
Two of the user's reports were **refutations**, and they are recorded as such:
the 1701 signature input they typed into is correct (F154 closed), and 0619E
item 6 is correct (F156 closed).

**What the review found, by mechanism:**

| Symptom the user saw | Mechanism | Finding |
| --- | --- | --- |
| tab order skips fields, jumps back up | bands emitted after the whole cells layer (`emit.py:5677` / `:5680-5698`); layout order is already exact reading order; no `tabindex` exists anywhere, so focus order IS DOM order | F209 |
| Schedule 1 Taxpayer/Spouse squares unfillable | squares drawn at gray 0.251 → `tone_role` decorative (`lattice.py:200`) → excluded from grid intake (`lattice.py:6295`) → area collapses to one `label` cell → refused at `emit.py:2326`. Their interiors carry white knockouts — the source saying "write here" | F210 |
| 0619E signature boxes unfocusable | one caption run inside a 302×43pt bordered box sets `is_empty=False` → `label` → refused. **0620 `p1c87` is the control**: same box, no interior caption, ships an input today | F211 |
| signature line wants bottom + centre | `.fh52` line-height = box height ⇒ vertically centred; no `text-align` emitted for plain fields | F212 |
| red/blue overlay marks on correct inputs | the overlay has NO tone awareness: grey 0.8509 tint fragments become walls. Proven: 1701 p2 `p2c21-i` reports `over=4.57pt` against tint fragment `h27`. **Not all blue is phantom — F210's blues are real** | F213 |
| p4 item 9 "(specify)" blank | caption cell is `label`; the strip below the rule is a 3.22pt sliver → `blank` (`lattice.py:3541`) | F148/F149 |

### T1 — emit band rows at their reading-order position (closes F209)

Split the cells layer around each band so the band lands at its own `(y0,x0)`:
`layer-cells` → band → `layer-cells`. Cells stay a flat sibling run; **no
`tabindex`** (the referee pins element attribute key sets exactly at
`comb_referee.py:1168-1177`, `:3293-3304`, `:3433-3441`).

Pre-flight, all three before writing code: (1) does `SlotParser`'s nesting
contract (`comb_referee.py:3400-3468`) accept multiple `cells` containers per
page — if not, extend it in the same commit; (2) does `audit.py`'s
`CELL_BOUNDARY_RE` (`:752-753`) over-run on the last cell before a split —
**`audit.py` is locked, so STOP and report rather than edit it**; (3) is
`applyFields`' input counting (`emit.py:4531-4545`) scoped to the band subtree
rather than the page.

**Acceptance:** with `row_tops`-style clustering (`band_drive.py:114-132`),
zero reading-order inversions on all 53 forms (non-zero before); inputs stay
45,333; no cell id/class/attribute/style moves; all self-tests +
`validate_tree` + `prove_fixtures_fail` pass; assertions unmoved at 3/6 and
10/19; determinism byte-identical across two `batch.py` runs; all 53 structure
hashes re-pinned with the cause named; `HTML_RUNTIME_SCRIPT_SHA256` must NOT
move unless script text changed.

### T2 — `tab_check.py`: the automated tab-walk with reviewable artifacts

Nobody hand-verifies a form again. Reuse `band_drive.py:401-432` (sync
Playwright, `file://`, pageerror listener), `:56-60` (`bundle_dir`), `:114-132`
(`row_tops`); `fill_check.py:156-190` proves keyboard/focus works headless.
Do **not** reuse `audit.py`'s Playwright runtime — it is a sealed
provenance-attesting harness whose page handle never leaves its worker
subprocess.

Per form: `goto(…, wait_until="load")` (sufficient — bands are pre-rendered at
capacity, no post-load mutation), inject ONE `focusin` recorder, Tab until
`activeElement === body`, read the sequence back in one `evaluate`. A hard
press cap being hit is a FAILURE, never a silent truncation.

Verdicts: `green` (reached in order), `red-skipped` (never reached),
`red-order` (reached out of order). **State the limitation plainly:** the walk
can only judge inputs that EXIST — a missing input looks identical to correct
paper. Missing-field detection belongs to T4's blue census and the ledger.

**Artifacts** → `forms/review/<slug>/`: `tab.json` (sequence, verdicts,
per-input page + pt geometry) and `page-<N>.png` with the verdicts burned in
(legible on a phone), plus a generated `forms/review/index.html`. Untracked,
**not** gitignored. `justfile`: `tab-check [slug]`, `review-clean`,
`review-serve`. One CI step after the self-test loop in `formgen.yml`
(Chromium already installed there; `PLAYWRIGHT_BROWSERS_PATH: '0'` matters).
**Not** in `gate.SELF_TEST_MODULES` (user decision) — but ship a cheap
`--self-test` so gating stays possible later.

**Acceptance:** self-test passes; 53 forms produce artifacts; **expect red on
the 22 band forms and report it as F209's evidence, not as flake**.

### T3+T4 — one overlay package, one re-pin (closes F213)

**T4.** The tone census this package was told to do first **has been done**
(2026-08-11, over all 53 `build/ir/*.ir.json`). Do not redo it; do not accept
the premise it corrects.

Rule tones are quantised to eight values, and **the interval this plan
originally expected to be empty is not**:

| gray | rules | box edge <20pt | 20–100pt | ≥100pt | forms | reading |
| --- | --- | --- | --- | --- | --- | --- |
| 0.0 | 49,962 | 40,288 | 2,940 | 6,734 | 53 | structural |
| 0.251 | 76 | 64 | 12 | **0** | 3 | **wall** — F210's squares |
| 0.502 | 164 | **164** | 0 | **0** | 11 | **wall** — 100% box edges |
| 0.651 | 405 | 380 | 18 | 7 | 26 | **wall** — 94% box edges |
| 0.7489 | 528 | 76 | 239 | 213 | 12 | **tint** |
| 0.7529 | 1 | 1 | 0 | 0 | 1 | tint side |
| 0.8509 | 2,256 | 741 | 559 | 956 | 39 | **tint** — the proven phantom source |
| 1.0 | 1,751 | 705 | 548 | 498 | 42 | knockout |

So 0.502 and 0.651 **are occupied and are walls**, not tint: they are the
mid-grey checkbox outlines on 11 and 26 forms. Splitting at 0.251 (or anywhere
below 0.651) would erase real checkbox boxes from the overlay and hide F210's
whole family.

**The split belongs in the unoccupied interval 0.651 → 0.7489** — genuinely
empty, 0.0979 wide, so a split at e.g. 0.70 lands between no two occupied
values. It is corroborated independently by ink morphology rather than resting
on the gap alone: below it ink is overwhelmingly short box edges (0.502 is
100% box edges, 0.651 is 94%, and neither 0.251 nor 0.502 has a single
page-spanning run); above it ink is dominated by page-spanning band edges
(0.7489 is 45% ≥100pt, 0.8509 is 42%). Two independent signals, same boundary.
This also puts F210's 0.251 squares firmly on the wall side, which T5a needs.

Then tint rects stop being wall candidates in `visibleRects`/`boxAt`,
and `vacant` also excludes boxes whose interior probe resolves to tint. Keep
the overlay's field-layer independence (`emit.py:7553-7556` asserts it reads
only `.rl`; `parentNode` to the `layer-{role}` group is the sanctioned role
source and is not banned). Extend the self-test literals and add a mutation
that trips on tint-as-wall. Fix the legend's per-box vs per-input counting
note; explain the orange TIN cluster (pre-printed constants + F208 outer
insets — expected, not a defect).

**T3:** a `?debug=tab` mode rendering `forms/review/<slug>/tab.json` — green,
red and sequence numbers from the JSON's own geometry, no field-layer reads.
Works served (`just review-serve`); on `file://` show a hint. **Fix
`review-serve` first**: T2 rooted it at `forms/review/`, but the viewer needs
`forms/<slug>/index.html` and `forms/review/<slug>/tab.json` under ONE server
root — re-root it at `forms/`.

**T3/T4 addendum (user, 2026-08-11):** the review surface should need no
manual tabbing AND no separate blue pass. After T4 makes `vacant`
trustworthy, `tab_check` burns the SAME tone-aware blue census into its
`page-<N>.png` alongside green/red, so one image per page answers both "does
tab order hold?" and "is anything printed still missing its input?".
`forms/review/index.html` is the single entry point. The user's manual
override loop (they name a field, we trace the mechanism) remains the escape
hatch for defect CLASSES no rule formalises yet — each such report must land
as a general rule plus a permanent check, never a one-field patch. Once T5c
stamps underscore-origin rules, add the corpus assertion that every such rule
inside a caption cell carries an input — F148's class becomes machine-checked
from then on.

**Acceptance:** 1701 p2 item-3 band shows zero red and no phantom blue, while
**F210's Schedule-1 blues STAY blue** (they are real until T5a lands); 0619E
items 3/4/12 reds gone; per-form blue counts published before and after.

### T5 — the missing-input family (measurement-first; `audit.py` locked)

- **T5c — DONE** (F148/F149 fixed; worktree `wt/t5c-ruled-blanks`). Every rule
  in `extract.py` now carries `origin` (`RULE_ORIGIN_TEXT_UNDERSCORE` for a
  `ruled_blank_bars` bar, `RULE_ORIGIN_VECTOR` otherwise; a merged bar is
  text-underscore only when EVERY contributor is), and `emit.py`'s
  `field_verdict` gives a `label` cell one input per structural,
  singly-owned underscore-drawn rule it carries (`RuledBlankWriting`,
  `ruled_blank_field_box` — one line tall, seated on the rule, x-extent the
  rule's own; a cell with more than one blank on its own line, e.g.
  "Page ___ of ___", gets one region per blank). Corpus: 118 underscore-drawn
  rules total, 60 structural (58 are `role: "knockout"` — white-on-colour
  legend lettering on 1600-PT/1600-VT/1606/1706, inside no lattice cell at
  all); of the 60, 1 is claimed by two `label` cells at once (2550Q p2's
  fraction bar under "Total Sales", refused rather than guessed at) and 58
  are singly-owned, spanning 54 cells across 19 forms. 54 cells / 58 inputs
  gained; `inputs_over_printed_text` **3/6 unmoved**,
  `comb_slots_match_printed` **10/19 unmoved**, comb censuses **4,587/33/4,554
  unmoved**, tab-walk **53/53 green** with every new input in reading order,
  inputs **45,333 → 45,391**, two `batch.py` runs byte-identical. New standing
  corpus-wide self-test `emit.ruled_blank_corpus_assertions` (run by
  `emit.py --self-test`) re-derives the claim set against every `build/ir`
  this checkout has and fails if any claimed cell lacks a typing surface —
  F148's class is machine-checked from here on. `EXPECTED_HTML_STRUCTURE_SHA256`
  re-pinned for the 19 moved slugs and `AUDIT_DEPENDENCY_SHA256["tools/formgen/
  extract.py"]` re-pinned in `comb_referee.py`; `audit.py` and `gate.py` pins
  untouched.
- **T5a** (F210). Two candidates, measurement decides: (i) a knockout
  interior inside a decorative rule box, checkbox-sized ⇒ field (F206's
  marker family); (ii) admit mid-tone rules into lattice intake at the split
  T4 measured. Census before implementing; **abort honestly if neither
  separates**. The four 1701 p2 squares are the anchor; survey the corpus for
  the same pattern.
- **T5b** (F211). Candidate rule: a bordered box (≥3 borders, ≥2 text lines
  tall) whose printed ink is confined to a top-left caption strip ⇒ `field`,
  writing box = the remainder below the caption
  (`writing_box_clear_of_printed_ink` already trims top ink). Measure against
  the known 1,875-label-blank trap. 0619E `p1c87/p1c88` and 0619F
  `p1c100/p1c101` must flip; **0620 `p1c87` must not change**; enumerate
  every other cell the rule would flip and review them. Abort if unseparable.
- **T5d last** (F212). Bind a signature strip by printed-caption evidence
  (the `BureauReservation` precedent): a caption below the box matching
  "Signature over Printed Name…" binds the ruled box above. Seat the input in
  a single-line strip at the cell bottom via `field_box` insets, and centre it
  with inline `text-align` — the referee's inline-style scan
  (`comb_referee.py:928-948`, `:3446-3465`) does not ban it, while the
  stylesheet allowlist (`:3607-3648`) rejects a new CSS class. Verify with T2
  screenshots.

---

## Log

- **2026-08-11** — User visual review of 1701 p1–p4 and 0619E. Packages
  T1–T5 appended above the log. Two findings **refuted** by the review and
  closed `not-a-defect` (F154, F156) — P5 asked for exactly this evidence.
  Five filed: F209 (band tab order, 490pt backwards jump on 1600-PT p1, 22
  forms), F210 (decorative-tone checkbox squares never segmented), F211
  (caption-in-box kills a 302×43pt signature surface; 0620 is the control),
  F212 (signature typography, minor), F213 (the overlay has no tone
  awareness, so it invents walls out of tint). F207 fixed and landed at
  `ae19137`: pre-printed ink is now measured from the ink band, not the run
  box — the sixth defect this session of the shape "an outer bound taken from
  a nominal edge instead of measured from the ink".
- **2026-08-10** — Implementation packages P1–P5 appended above the log,
  written for mechanical execution by implementing agents. P1
  (knockout-bitten walls, F097) fully diagnosed: 8-bite corpus census, the
  strict-coverage and same-axis discriminators, and three measured negative
  cases (2200A junction, 1800 abutting witness, doorway). P3 re-scoped
  upstream after the reverted emit-side attempt (F200). P2 gated on a
  bimodality measurement with an explicit abort. Diagnosed at `5cd4017`,
  gate r36 10/13.

- **2026-08-08** Batch-versioned immutability reconciled with the user and recorded (ARCHITECTURE.md): stage-1 batches freeze per scored gate, stage 2 applies records to a named batch, uncorrected forms byte-copy, gate runs on both trees. One true stage-2 record known: 2550M TIN 3->5.
Newest first. One line each.

- **2026-08-07 (r23)** — **The three regressed assertion families, paid; two
  are green and the third is back to r20's form count.** `emit.py` now lays
  every rectangle it DRAWS for a comb — the slot div, the input inside it, the
  band-template JSON a cloned row is re-laid out from, and the face
  `field_box` fits — out on the WRITING box through one function,
  `comb_writing_rect`, while the divider band survives emission unmodified for
  `comb_referee.classify_band` and the reviewed 2551Q control. 2550M's item-4
  TIN compartments are **14.16pt inside a 15.60pt row again, not 3.12pt**
  (F186 closed on the shipped bytes and on a 3× screenshot). That corpus
  change alone took `comb_slots_match_printed`'s `invalid-emission` population
  **64 offenders on 25 forms → 3 on 3** with `audit.py`'s source-occupancy
  query untouched: it had been asking the source about a 3pt band where the
  printed constant is not. `money_boxes_have_inputs` 4 → **0** and
  `printed_box_peers_all_fillable` 1 → **0**; the only exclusion added,
  `audit.source_bureau_reservations`, reads the sheet's own
  "(To be filled up by the BIR)" from the pinned PDF's text operators — not
  from `emit.BureauReservation`, not from the IR — reports the matching
  phrase's rectangle and never its line (0605 sets two captions on one
  baseline, and a line-wide rectangle would excuse the taxpayer's Return
  Period boxes; a mutation to it fails two new self-test assertions), claims
  **exactly ONE box corpus-wide**, and publishes `boxes_bureau_reserved`
  declared in `gate.BASIC_ASSERTION_COUNT_FIELDS`. **Reported loudly:
  `inputs_over_printed_text` got WORSE — 19 forms/131 → 20/147, +21 new and −5
  cleared** — and every one of the 21 is G05's existing caption-plus-comb
  population, cell for cell, that r22 had hidden rather than fixed. **F187**
  files the residue: 2200-A/C/P's Bureau band still reports one compartment to
  `comb_slots_match_printed`, which asks for ink and cannot see a caption; not
  fixed here because that assertion's shape is contract-bound by the referee.
  Census: **no comb pin moved and none should have** — `lattice.py` is
  byte-identical, 4,583 subjects / 4,561 active / 22 retained, 45,765 inputs
  and 40,213 slot divs all unchanged. All 53 `EXPECTED_HTML_STRUCTURE_SHA256`
  and `AUDIT_PRODUCER_SHA256` moved; `HTML_RUNTIME_SCRIPT_SHA256` was
  re-derived and did not. The 53-document review is the strongest yet: **tag
  inventory delta ZERO for every tag name, 239,562 elements before and after,
  visible text token-for-token identical**, the whole change being slot-div
  style attributes. 33 → **32 blocker+major open of 129**. No check, tolerance
  or assertion was weakened. **Gate r23: 9/12, the same three red as r22, and
  `money_boxes_have_inputs` and `printed_box_peers_all_fillable` are GONE from
  the `assertions` detail — the full clean-tree gate confirming 4 → 0 and
  1 → 0. Determinism `ba1bd2d8c47e`, moved and had to. The comb referee is
  UNEVALUABLE for character-for-character r22's reason (2550M `p1c89`/`p1c90`,
  F184's cells) and r23 neither cleared nor worsened it.**

- **2026-08-07 (r20)** — **`printed_box_peers_all_fillable` PASSES, 14 of 53
  forms → 0, with `audit.py` byte-identical (`8d22a957…`) throughout.** Two
  producer bugs in `lattice.py`: `GroupGeometry.span` filtered a cluster's
  coverage by distance to the cluster's own *mean* centre and so could drop a
  rule that is itself a member (0619-E's Amended-YES wall, 0.35 against a 0.30
  tolerance, merged the box into its caption); and `assign_points` placed a text
  run by its bounding-box centre, which is the run's ADVANCE, so the whitespace
  in `Calendar        Fiscal` counted as printed text inside the checkbox drawn
  in the gap. `glyph_ink_spans` reads the per-character origins the IR already
  carries. **`extract.py` now models PDF 32000-1 §8.4.3.3 line caps** — 340 of
  569 open strokes in this corpus carry a round or projecting cap and were being
  published 0.36pt short at each end, which is how 2550M's four year boxes
  reached the taxpayer as one input — proven by a written-here 200×200 probe
  page with 13 asserted cases and a mutation that restores the old behaviour.
  **15 findings closed on measurement** (F049, F054, F058, F062, F106, F135,
  F150, F152, F153, F173–F177, F180), each against its own coordinates in the
  shipped bytes and by a checker that never consults the r20 audit, so a box
  that cleared because its row *peer* lost an input would still read as
  uncovered. 55/126 → **42/128** blocker+major open. **Reported loudly:**
  `comb_slots_match_printed` got worse by 3 — five money-comb cells on 2550M
  claim a compartment the sheet does not print, filed as **F184** / new row
  **G19**, and deliberately not patched because the fix is the reviewed
  `retired_proven_false` transition. **F185** files the 11 comb-spanning inputs
  the cap model made visible. A third `lattice.py` fix was needed on the way:
  the first regeneration made that assertion worse by 13, not 3, because a
  suppressed subject's `mapped_partition_cell_ids` is a partition and nothing
  enforced it — 2550M's `p1c7` nests inside `p1c6` and both claimed three cells,
  which correctly invalidated the whole form's owner registry.
  `resolve_retained_partition_overlaps` gives a contested cell to the smallest
  claiming area; corpus-wide 3 cells, one page, one form, no mapping emptied.
  **Census: `EXPECTED_COMBS` 4,538 → 4,543, retained 17 → 21 on six slugs, 25 of
  53 `EXPECTED_HTML_STRUCTURE_SHA256`, both producer SHAs — and
  `gate.EXPECTED_COMB_SUBJECTS` 4,521 → 4,543, which was ALREADY WRONG at HEAD**
  because r19 moved its twin and not it. `comb_referee`'s own self-test caught
  the census move exactly as designed (its retained-one fixture slug 2551M went
  to retained-three), so the fixture rotated to 1604-CF and 2551M became the
  retained-many negative control. No check, tolerance or assertion was weakened.
- **2026-08-07 (r18)** — **The audit can see the field layer. G10 moves from
  `open` to `fixing` with its first two assertions.** `inputs_span_no_printed_divider`
  walks 44,536 emitted inputs and asks the pinned PDF whether it drew a
  compartment divider inside one: **79 offenders on 11 of 53 forms**.
  `printed_box_peers_all_fillable` recovers 7,223 printed boxes from the source's
  own paint stream and reports a box with no input whose identical row peer has
  one: **14 offenders on 14 of 53 forms**. Neither reads the layout, the plan,
  emit.py's markers or the IR — the population that was blind was blind precisely
  because the two nearest existing assertions enumerate from the producer that
  made the mistake. **These 93 offenders are newly VISIBLE, not new**; every one
  was in the shipped corpus at r14. 16 of them independently re-derive existing
  human findings at the same coordinates (0619-E's A10 box matches F152's
  reviewer-measured `(276.0, 135.0) 12.5 x 10.5`; 2550M's A9 `p1c2` is G02a), and
  **9 were on populations no open finding covered: F173–F181** — five checkboxes
  that make a required election unstateable (1701 ATC II016 Mixed Income 8%;
  1701MS spouse OSD; 1706 item 11 treaty "No"; 2200M item 12 treaty "No"; 2550Q
  **2nd quarter**) and four comb-spanning input groups including the TINs of
  1600WP and 2553. `gate.py` grew its two allowlists, its fixture and its count
  literal (8 → 10) in the same commit, plus a new self-test invariant so the next
  assertion cannot be added without its count contract.
  `comb_referee.AUDIT_PRODUCER_SHA256` `d31b4d7a` → `8d22a957`. **No census pin
  moved and none should have** — no generator changed, `batch.py` re-converted
  53/53 byte-identical and `forms/index.html` regenerated byte-identical.
  **Reported loudly: PT 060 still reads 5% and is officially 2%.** The guide
  reflow fix did not land (`guides.py` byte-identical to r14; the work was
  reported `fixed: false`), so **F127 is REOPENED** with the retraction in its own
  resolution — its closure measured prose flattening, which really is gone, while
  the code-to-rate association it says was destroyed still is. Blocker+major open
  49/116 → **59/125**, and going up for these two reasons is the ledger working.
  One cosmetic defect found and NOT fixed here because `audit.py` is another
  agent's file: `audit.py:13516` prints `assertions {n}/8` from a literal, so the
  console now reads `10/8`. Console-only, no check reads it, but it is a stale
  census literal of exactly the G01 shape and should derive from
  `len(ASSERTION_KEYS)`. **Gate r18: 9/12, three red, the same three as r17 —
  determinism `8ceeab9e506d`, identical to r14/r15; both pre-existing assertion
  counts unmoved at 20 and 22; the referee's UNEVALUABLE is exactly r17's
  residue on 1604C, 1700, 1701MS, 1702EX, neither cleared nor worsened, and
  still undiagnosed.**
- **2026-08-07 (r14)** — **G11 fixed and G15 closed; 332 inputs removed across
  35 of 53 bundles.** `emit.comb_slot_verdicts` decides per COMPARTMENT, never
  per group: a slot is refused when the source printed exactly one alphanumeric
  glyph wholly inside that slot's own walls, or shaded it at the unchanged 0.87
  threshold. 281 compartments refused across 26 forms, spelling only constants —
  `00000` ×42, `20` ×4, `II011`, `XC010`, `VN010`, `WI165`, `039`, `250000` and
  13 grey separator/caption compartments. 2000-DST's money grid keeps all 14
  compartments of every money comb including the printed decimal bullet, so C4
  is intact; 1600-PT's year comb keeps its two YY boxes while refusing the
  century, which is the case that forces per-slot. The other 51 removals are
  `lattice.covering_shading_band` landing: cells sitting on official grey
  "no entry applies" bands, confirmed against the pinned PDF by rasterising
  2200T page 2's Part V header. **F139–F146 and F172 resolved; 58 → 49
  blocker+major open of 116.** Three pin faults found and fixed on the way, all
  of them stale AT HEAD rather than caused here: `EXPECTED_COMBS` /
  `EXPECTED_COMB_SUBJECTS` 4540 → **4521** (21e0630's shaded-paper fix removed
  19 combs without its census — G01 repeating one commit later, and it would
  have failed r14 after 60 minutes); `guides.py` `("2550m-2007", 3)` 1 → 0; and
  all 53 `EXPECTED_HTML_STRUCTURE_SHA256`, which had been stale since GOAL.md
  §Blocked and had been making the comb referee UNEVALUABLE every run. The
  refresh was reviewed, not rubber-stamped: a tag/attribute diff of all 53
  emitted documents against their HEAD selves shows 332 `<input>` deleted, zero
  elements added, and nothing else moved. **New row G16**: the fix's unpaid half
  — `audit.py`'s `comb_slots_match_printed` demands contiguous input indexes, so
  it now reports 76 new offenders for compartments that are correctly empty.
  The assertion was NOT weakened and must not be. **New row G17**: r14's
  referee UNEVALUABLE turned out to be a THIRD reviewed emitter pin,
  `HTML_RUNTIME_SCRIPT_SHA256`, which only the referee reads and which was
  absent from the pins-move-together list. Two of its three hashes moved and
  exactly the two this fix touches — the field runtime and the debug overlay —
  while the band-data runtime is byte-identical. It is re-pinned; **that re-pin
  carries no verdict and the next full gate settles it.**
- **2026-08-07** — Nine-reviewer sweep of all 53 forms against the official PDFs
  consolidated. **34 findings appended, F139–F172**, all `open`; the 138
  immutable entries and the `cause_codes` block were not touched and the pinned
  digest still matches, so the ledger grew in place and no side file was needed.
  Five new defect classes: **G11** (a lattice-`mixed` cell — pre-printed ink
  inside — still gets a full set of editable comb slots: 180/180 such cells,
  175 slots on a short pre-printed constant across 24 forms, including the
  statutory ATC codes `II 011` and `XC 010`), **G12** (caption swallows the
  writable blank → no input), **G13** (multi-column guide reflow interleaves
  columns; **2551M's guide binds a 5% rate to PT 060, officially 2%, on a
  finding already marked `fixed`**), **G14** (a BIR-only box is fillable),
  **G15** (the shipped `?debug=fields` overlay is the old self-referential one
  in all 38 bundles). G03 and G04 got their first measured denominators: 160
  empty non-fillable `label` cells across 38 of 53 forms, and 169 inputs sitting
  wholly on official grey decoration across 22 of 46 measured forms.
  Three instrument errors found and corrected in the consolidation's own tools,
  recorded here because each would have shipped a wrong number: assuming a
  612pt page width inflated "inputs on printed ink" 8× on landscape bundles
  (156 → 19); 12 of the surviving 19 were comb tick-marks, not text (19 → 7);
  and one evidence image was misread as "2552's Amended-Return YES checkbox has
  no input" when `p1c10` and `p1c11` both carry inputs — the old overlay simply
  does not outline every input. Reviewer reports flagging the relocated tax
  tables as "missing" from 1700/1701/1701A/1701Q/1701MS were checked and
  **rejected**: that is F028's fix working, the tables are in `guide.html` with
  0 inputs and no orphan frame remains; only the dangling "refer to tax table
  below" cross-reference survives (F171, minor).
- **2026-08-18** — Stage 3 preconditions 1–3 hold: catalog 9990/9990, G02/G03/G04
  `done`, ledger-check 259/259. Mapper still **Blocked. Do not start.**
- **2026-08-18** — Re-measured G02/G03/G04 on batch `ddac6058` (assertions-only
  on `forms/`, four font-snapshot misses re-scored). `inputs_span_no_printed_divider`
  0/53, `printed_box_peers_all_fillable` 0/53. Named G02a–i, G03, G04, G12, G14
  findings are `fixed` or `not-a-defect`. No producer residue and no new `forms/`
  batch. G16 stays open at 9 forms / 288 offenders and is not a Stage 3 gate.
  1801 tin-1 leftover is identity `extra/1801-2018/p1/tin-strip`, not a G02 merge.
- **2026-08-06** — Plan created at HEAD `0ea1f84`. Three stages recorded in
  ARCHITECTURE.md. Baseline measured: 53 bundles / 50 codes / 116 pages;
  gate r13 9/12; 26/84 blocker+major open. G01 (census pin contradiction
  4442 vs 4540) found and **not yet fixed**. `ddce158`'s referee claim is
  unreproduced on disk.
