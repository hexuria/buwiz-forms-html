# Brief for Grok — Stage 3 join-policy analysis (eBIRForms formgen)

You are doing a **read-only analysis pass**. The authoritative spec is
`tools/formgen/HANDOFF-STAGE3-JOIN.md` in the worktree below — read it
first and in full. This brief orients you and restates the hard limits;
where the two disagree, the handoff wins.

**Deliverable:** a written plan (markdown, returned in chat) that Uriah
pastes back into his `tin-stage2` chat. Not a PR. Not code. Not commits.

## Where you work

- Worktree: `/Volumes/goldcoders/reverse-engineer-ebir-forms/bir/.claude/worktrees/tin-stage2`
- Branch: `gol/stage3-ready`. Do **not** rebase, and do not mix commits
  with `public/main` — this branch is parallel to the squash merges there
  (merge-base is PR #20, `860c3910`).
- Prefix repo/shell commands with `rtk` (`rtk git status`). Formgen
  scripts run as `python3 tools/formgen/…`.
- `forms-corrected/` is gitignored. If you need it, rebuild with
  `python3 tools/formgen/correct.py --batch ddac6058`.

## Hard limits (fail closed — any violation invalidates the pass)

1. **Do not start the mapper.** No mapper module, no prototype of one.
2. **Do not write `name="frm…"` anywhere**, and stay out of `emit.py`'s
   `name=` logic. Today `name=` is the cell id (slot id on combs), and it
   stays that way this pass.
3. **No code changes, no commits.** This pass has no writer. (If anyone
   codes later: one writer only on `lattice.py`/`emit.py`/`extract.py`.)
4. **Never invent an `official_field_key`.** A record either harvests a
   unique key or carries a non-empty `official_field_key_gap`.
5. Keep every gate and assertion at current strength: `tab_check`, G16,
   the numeric 1% print gate, all coverage pins.
6. Identity ids stay durable: catalog `id` + `source_printed_box_pt` are
   the identity; never `@bbox` or bare `p1cN`.
7. Leave `~/Library/Group Containers/group.dev.goldcoders.bir/bir_data.db`
   untouched. No official submission path. No real taxpayer data.
8. The validation-rules subsystem (`rules/` runtime, `bir-rules*` crates)
   is a **separate objective** — do not touch it; 2550Q v2 stays test-only.
9. `tools/formgen/review/serve_tin_sitting.py` is local-only; never commit it.
10. Print-parity work is structure, never text pixels (all source PDFs
    have substituted fonts — text-pixel parity is proven unwinnable).

## The mission in one paragraph

The identity catalog (`tools/formgen/identity/catalog.json`, 9990 records,
schema `1.0.0-provisional`) names every fillable box durably, but it is a
**subject list, not a join**. The official eBIRForms XML keys already
exist as `serialized_key` in `rules/forms/*/fields.json` (never re-derive
them from raw save XML). Only 163 catalog records carry an
`official_field_key` (all via unique TIN-suffix harvest); 9827 are
honestly gapped. Stage 3 (the mapper) stays Blocked not for lack of code
but for lack of **policy**: nobody has written the rules for how a catalog
record joins a `serialized_key`. Your job is to write that policy, backed
by re-measured numbers, and to propose exactly one next increment that is
not "start the mapper".

## Read order (from the handoff — stop after these)

1. `tools/formgen/HANDOFF-STAGE3-JOIN.md` (the spec)
2. `tools/formgen/PLAN.md` — Active queue (top) + "Stage 3 — map" rows;
   later STATUS.md/GOAL.md numbers are mixed vintages
3. `tools/formgen/ARCHITECTURE.md` — stage split; "Stage 3 binds to
   `forms-corrected/` and to nothing else"
4. `tools/formgen/identity/README.md` + `identity/schema.json`
5. `tools/formgen/identity/mint_fillables.py` — `harvest_tin`,
   `default_harvest`, `inventory_path_for_slug`, `SKEW`
6. `tools/formgen/field_identity.py` — matcher, coverage pins
7. `tools/formgen/corrections/README.md` — C01–C07 are `applied`, not
   `verified`
8. A few `rules/forms/*/fields.json` (one TIN-rich, one with many
   `serialized_key: null`, one `extra/` HTML bundle with no inventory)
9. `tools/formgen/emit.py` — what `name=` is today

## Work item 1 — census (re-measure everything you cite)

The handoff's own numbers are dated 2026-08-19 and PLAN.md's Stage 3
table is dated 2026-08-06; **re-measure both, trust neither**. Produce a
replayable census:

- Catalog slug → `rules/forms/*/fields.json`: hit / miss / year-or-suffix
  skew — from the actual files, **not** `inventory_path_for_slug` (it is
  not a census: it misses `0605-1999` → `0605-v2003`,
  `1702ex-2018` → `1702ex-v2018c`, several `extra/` slugs, attachments).
- Per bundle: fillable identities, keyed identities, `fields.json` row
  count, null `serialized_key` count, unique HTML `name=` count.
- Cardinality buckets, each with at least one named example: 1:1, N:1,
  1:N, 1:0 (identity with gap), 0:1 (leftover XML key), growable/repeat.
- Which gap strings are mint-path false negatives (inventory exists but
  the slug match failed). Known case: 0605 — 61 identities say "no
  harvested fields.json" while `0605-v2003/fields.json` has 235 rows with
  0 nulls.

Done when every number is dated to this run and each PLAN.md 2026-08-06
row is marked **confirmed** or **superseded**.

Reference values from the handoff (2026-08-19, re-verify): catalog 9990
records / 53 bundles; fields.json 43 forms / 9592 rows / 1234 null keys;
163 keyed, 9827 gapped (6221 "no unique key", 3601–3605 "no harvested");
families text 4779, comb 1999, money 1646, date 704, xbox 654, tin 208;
0605 HTML has 121 `<input>`, 67 unique `name=`, 210 cell ids.

## Work item 2 — the join policy

Decide, with evidence, all seven:

1. When a harvested `serialized_key` may be copied onto HTML `name=` vs
   staying a catalog-only annotation.
2. How to pick among many candidate keys for one identity (TIN-suffix
   uniqueness is the only harvest that works today).
3. How to compose many cells → one XML field, and the inverse. Note: the
   TIN is four keys (3+3+3+5), so it is **not** the N:1 example — money
   combs and growable rows are.
4. What to do with `official_field_key_gap` (never invent a key).
5. Growable bands / repeating rows vs one XML list field /
   `serialized_occurrence`.
6. Binding: `forms-corrected/` only, and only after a correction exists.
7. What "leftover" means on both sides (1:0 and 0:1) and which leftovers
   may ship. Known honest leftovers: `extra/1801-2018/p1/tin-strip`
   (one mixed cell, four HTA keys); 0605's 235 keys vs 65 fillable
   identities.

Done when a reviewer can classify a new box without a judgement call the
policy does not name. If a case is undecidable, the policy says it stays
gapped and names the evidence that would un-gap it.

## Work item 3 — one serial next increment

Exactly one, and it must not be "start the mapper". Candidates the queue
already implies: a schema/docs artifact for the join, a replayable census
script, a fixture proving the policy can **fail**. Name its completion
command or artifact, the files it would touch, and state explicitly that
emit/`name="frm…"` stays untouched.

## Return format — four sections, in this order

1. **Join policy** — the rules, one example per cardinality case.
2. **Census** — live table; each PLAN.md 2026-08-06 row confirmed or
   superseded.
3. **Next increment** — one step, completion criterion, files touched.
4. **Still blocked** — the mapper; G16 `comb_slots_match_printed`
   (9 forms / 288); C01–C07 `verified` (they are only `applied` —
   sitting approved 2026-08-18, `proven: false` until `audit.py` is seen
   to fail on `forms-corrected/` for the declared divergences); anything
   else your policy leaves closed.

No mapper code. No `name="frm…"`. No commit unless Uriah asks after
reading the plan.
