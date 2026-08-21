# Handoff — Stage 3 join policy (Fable)

Read-only analysis. Return a plan Uriah can paste back into the
`tin-stage2` Cursor chat. **Do not start the mapper. Do not write
`name="frm…". Do not land code unless this document's return format
says otherwise (it does not).**

Dated 2026-08-19. Re-measure any number you cite.

## Mission

Decide **how** an identity catalog record joins to an eBIRForms
`serialized_key` — cardinality, when a key may be copied onto HTML
`name=`, what leftovers are, what stays a catalog-only annotation.
That written policy is the deliverable. A better serial increment
follows from it. The mapper stays closed.

Completion: a plan with (1) a join policy a reviewer can apply to a
new box without guessing, (2) live vs stale measurements on this
tree, (3) one next increment that is not “start the mapper”, (4)
what stays blocked.

## Where

Worktree:

`/Volumes/goldcoders/reverse-engineer-ebir-forms/bir/.claude/worktrees/tin-stage2`

Branch `gol/stage3-ready`. Authoritative clone for landing is
`/Volumes/goldcoders/reverse-engineer-ebir-forms/bir` on `main`.
Working remote `public` → `hexuria/buwiz-forms`. Prefix repo commands
with `rtk`. Formgen scripts are `python3 tools/formgen/…`.

This worktree’s identity commits are **parallel** to the squash
merges on `public/main` (`868260c5` = PR #23). Merge-base with
`public/main` is PR #20 (`860c3910`). Analyze this worktree’s
`catalog.json`. Do not mix commits with `public/main` and do not
rebase.

## Read in this order

1. This file.
2. `tools/formgen/PLAN.md` — **Active queue** (top) and **Stage 3 —
   map**. Queue is live; later STATUS.md / GOAL.md numbers are mixed
   vintages. GOAL.md still talks about PR #13.
3. `tools/formgen/ARCHITECTURE.md` — stage split and “Stage 3 binds
   to `forms-corrected/` and to nothing else”.
4. `tools/formgen/identity/README.md` + `identity/schema.json`.
5. `tools/formgen/identity/mint_fillables.py` — `harvest_tin`,
   `default_harvest`, `inventory_path_for_slug`, `SKEW`.
6. `tools/formgen/field_identity.py` — matcher, coverage pins.
7. `tools/formgen/corrections/README.md` — C01–C07 `applied`, not
   `verified`.
8. A few `rules/forms/*/fields.json` (one TIN-rich, one with many
   `serialized_key: null`, one extra/ HTML bundle with no inventory).
9. `tools/formgen/emit.py` — `name=` today is the cell id (and slot
   id on combs), not an official key.

Stop there unless a claim in those files is stale against the bytes.

## Frozen (preconditions 1–3 hold)

Three stages to a submittable form:

```
STAGE 1  GENERATE   pinned PDF → IR → lattice → emit → forms/   (batch-versioned)
STAGE 2  CORRECT    forms-corrected/ = copy(batch) + correction records
IDENTITY            catalog of durable fillable ids
STAGE 3  MAP        identity → eBIRForms XML key, bound to forms-corrected/ only
```

Stage 2 is facts the source cannot tell us (2007 artwork prints 3
branch boxes; 2018 filing wants 5). Stage 1 is us misreading a
correct source. A correction never hides a divergence.

Landed on `public/main` (do not fold new work into these):

| PR | What | Merge |
| --- | --- | --- |
| [#17](https://github.com/hexuria/buwiz-forms/pull/17) | TIN 3-3-3-5 chrome | `d248eb21` |
| [#18](https://github.com/hexuria/buwiz-forms/pull/18) | Thick rails / skip-fuse | `6ab185e7` |
| [#20](https://github.com/hexuria/buwiz-forms/pull/20) | `maxlength=1` charboxes and X-squares | `860c3910` |
| [#21](https://github.com/hexuria/buwiz-forms/pull/21) | Named batch `ddac6058`, C01–C07 re-anchor, tab-order DOM sort | `ba78360b` |
| [#22](https://github.com/hexuria/buwiz-forms/pull/22) | Field-identity catalog (TIN first) | `659e72e8` |
| [#23](https://github.com/hexuria/buwiz-forms/pull/23) | Rest of fillables 9990/9990; mapper not started | `868260c5` |

Identity freeze:

- Catalog `tools/formgen/identity/catalog.json`: **9990** records,
  schema `1.0.0-provisional`.
- Pins: `EXPECTED_FILLABLE_CELLS = 9990`,
  `EXPECTED_UNCATALOGUED_FILLABLES = 0` in `field_identity.py` and
  `gate.py`.
- Identity `id` (example `2550m-2007/p1/tin-branch`) is durable.
  Artwork identity is `source_printed_box_pt`. `html_id_hint` is the
  current `p1cN` and is **non-authoritative**.
- Matcher: exactly one fillable-field **center** in
  `source_printed_box_pt`. Zero = `unresolved`, two = `ambiguous`,
  unique but different id = `html_id_hint_stale` (update the hint,
  do not mint a new identity).
- `official_field_key` is harvested when one unique key exists;
  otherwise a non-empty `official_field_key_gap`. Never invent a key.
- C01–C07: status `applied`, **not** `verified`. Uriah sat
  http://127.0.0.1:4191/ on 2026-08-18 and approved chrome + lock.
  Lock: C01 (2550M) and C07 (1604CF) lock branch `00000`; C02–C06
  editable. Verification = `audit.py` seen to fail on
  `forms-corrected/` for declared divergences (`proven: false` until
  then). Out of scope for unblocking the mapper.

PLAN.md active queue: *“Preconditions 1–3 hold. Mapper still
Blocked. Do not start. Comb-referee PASS, G10/G16/G17, and C01–C07
verified are out of scope.”*

## Why the mapper is still Blocked

The catalog is a **subject list**, not a join. Payload XML keys
already live in `rules/forms/*/fields.json` as `serialized_key`.
**Do not re-derive names from raw save XML.**

Join is not 1:1. Harvest uniqueness today is almost only TIN
suffixes (`harvest_tin` in `mint_fillables.py`). I2 cells used
`default_harvest`, which writes a gap unless a unique TIN-style
match already exists.

Live on this tree, 2026-08-19 (re-measure if you cite):

| Fact | Measure |
| --- | --- |
| Catalog records | 9990 |
| Bundles in catalog | 53 |
| `rules/forms/*/fields.json` | 43 forms, 9592 field rows, **1234** `serialized_key: null` |
| Catalog with `official_field_key` set | **163** (all unique; all TIN-suffix harvest) |
| Catalog gapped | **9827** |
| Gap “no unique fields.json key for this box” | 6221 |
| Gap “no harvested fields.json in this checkout” | 3601–3605 depending on leftover TIN |
| Families | text 4779, comb 1999, money 1646, date 704, xbox 654, tin 208 |
| 0605-1999 identities | 65 fillable (4 TIN keyed; 61 “no harvested”) |
| 0605-v2003 `fields.json` | **235** rows, **0** null `serialized_key` |
| 0605 HTML | 121 `<input>`, 67 unique `name=`, 210 cell ids |

Stale, do not reuse without re-measure:

- PLAN.md Stage 3 table (2026-08-06): “13 of 53 have no
  `fields.json`”, “8 joinable codes with revision skew”, “0605:
  71 vs 235”. Those vintages predate the catalog.
- `mint_fillables.inventory_path_for_slug` is **not** a census of
  whether `fields.json` exists. It misses `0605-1999` →
  `0605-v2003`, `1702ex-2018` → `1702ex-v2018c`, several `extra/`
  slugs, attachments. TIN seed harvested 0605 anyway; I2 mint then
  labelled the rest “no harvested fields.json” on a form that
  **has** 235 keys. Treat gap strings as mint-path output, not
  inventory truth.
- Explicit `SKEW` map is only two entries (`1601eq-2019`,
  `extra/1700-2018`). Other year/suffix skew is ad hoc in the
  matcher.

Cardinality cases the policy must name (examples, not a complete
list):

- **N HTML cells → 1 XML field** — TIN 3+3+3+5 is four keys, not
  one, so this is not “the TIN”; money combs and growable rows are
  the real N:1.
- **1 cell → N keys** — mixed strip `extra/1801-2018/p1/tin-strip`
  (honest leftover: four HTA keys do not collapse onto one mixed
  cell).
- **1:1** — TIN suffixes when harvest is unique.
- **0:1 leftover XML** — `fields.json` rows with no identity
  (0605: 235 keys vs 65 fillable identities).
- **1:0 leftover HTML** — identity with a gap; never invent a key.
- **Growable bands** — repeating HTML rows vs one XML list field /
  `serialized_occurrence`.

Stage 3 binds **`forms-corrected/` only**, never raw `forms/` after
a correction exists. `forms-corrected/` is gitignored; rebuild with
`python3 tools/formgen/correct.py --batch ddac6058`.

## Guards (fail closed)

State the target, then the hard stop.

- Produce a **join policy** and a **next increment**. Stay out of
  `emit.py` `name=` and out of a mapper module.
- Keep `tab_check`, G16, the numeric 1% print gate, and every
  assertion at current strength.
- Keep identity ids durable: catalog id + printed box, never `@bbox`
  or bare `p1cN` as identity.
- One writer on `lattice.py` / `emit.py` / `extract.py` if anyone
  later codes; this pass has no writer.
- Leave `~/Library/Group Containers/group.dev.goldcoders.bir/bir_data.db`
  untouched. No official submission path. No real taxpayer data.
- Print-parity: structure, not text pixels (substituted fonts).
- Validation-rules (`bir-rules*`) is a separate objective. 2550Q v2
  stays test-only.

Out of scope this pass: G16 `comb_slots_match_printed` (9 forms /
288, stays open); comb-referee remainder; C01–C07 `verified`;
starting emit of `name="frm…"`.

Parked, unrelated: 1601C desktop on `gol/1601c-desktop`; untracked
`HANDOFF-TIN-MODERNIZATION.md` and `skills-lock.json` on the main
worktree.

Sitting server `tools/formgen/review/serve_tin_sitting.py` is
local-only; do not commit it.

## Steps

### 1. Re-measure the join, do not trust the Stage 3 table

On this worktree, produce a census a reviewer can replay:

- Catalog slug → `rules/forms/*/fields.json` (hit / miss / year or
  suffix skew). Use the files, not `inventory_path_for_slug` alone.
- Per bundle: fillable identities, keyed identities, `fields.json`
  row count, null `serialized_key` count, unique HTML `name=`
  values.
- Cardinality buckets with at least one named example each: 1:1,
  N:1, 1:N, 1:0, 0:1, growable/repeat.
- Which gap strings are mint-path false negatives (inventory exists
  but slug match failed).

Done when every number in the plan is dated to this run and PLAN.md
2026-08-06 rows are either confirmed or marked superseded.

### 2. Write the join policy

Decide, with evidence, all of:

1. When a harvested `serialized_key` is copied onto HTML `name=` vs
   left as catalog-only.
2. How to pick among many keys for one identity (TIN suffixes are
   the only uniqueness harvest that currently works).
3. How to compose many cells → one XML field (and the inverse).
4. What to do with `official_field_key_gap` (never invent a key).
5. Growable bands / repeating rows vs a single XML list field.
6. Binding: `forms-corrected/` only, after a correction exists.
7. What “leftover” means on both sides, and which leftovers are
   allowed to ship.

Done when a new box can be classified without a new judgement call
that the policy does not name. If a case is undecidable, the policy
says it stays gapped and names the evidence that would un-gap it.

### 3. Propose one serial next increment

Not “start the mapper”. Candidates the queue already implies:
schema/docs for the join, a replayable census, a fixture that
proves the policy can **fail**. Pick one. Say what stays blocked
until it lands.

Done when the increment has a completion command or a named
artifact, and an explicit non-start of emit/`name="frm…"`.

## Return format

Bring this **back to Uriah’s `tin-stage2` chat**, as a plan, not as
a PR:

1. **Join policy** — the rules, with one example per cardinality
   case.
2. **Census** — live table; mark PLAN.md 2026-08-06 rows superseded
   or confirmed.
3. **Next increment** — one serial step, completion criterion,
   files it would touch.
4. **Still blocked** — mapper, G16, C01–C07 `verified`, anything
   else the policy leaves closed.

No mapper code. No `name="frm…"` in emit. No commit unless Uriah
asks after reading the plan.
