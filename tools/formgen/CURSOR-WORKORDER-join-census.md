# Work order — build `tools/formgen/join_census.py`

Execute exactly this one increment. It is the single unblocked step out of
the Stage 3 join analysis dated 2026-08-19. Everything else in Stage 3
stays closed.

Worktree: `.claude/worktrees/tin-stage2`, branch `gol/stage3-ready`.
Prefix repo commands with `rtk`. Scripts run as `python3 tools/formgen/…`.

## Hard limits

- **Do not start the mapper.** No mapper module, no prototype.
- **Do not write `name="frm…"` anywhere.** Do not open `emit.py`,
  `lattice.py`, `extract.py`, or `field_identity.py` for editing.
- **Never invent an `official_field_key`.** The census reports keys that
  exist; it never synthesises one.
- Do not modify `catalog.json`. This increment is read-only against it.
- One writer: this task creates exactly one new script and one evidence
  JSON. Nothing else changes.
- Do not rebase or mix commits with `public/main`.
- Leave the live DB alone. No submission path. No real taxpayer data.

## Deliverable

`tools/formgen/join_census.py` — read-only, replayable, deterministic.
Plus one evidence file under the existing convention
`tools/formgen/corrections/evidence/join-census-20260819.json`.

## Inputs

- `tools/formgen/identity/catalog.json` → `["records"]`, 9990 records.
  Fields used: `id`, `bundle_slug`, `role`, `official_field_key`,
  `official_field_key_gap`.
- `rules/forms/*/fields.json` → 43 files. Rows are `["fields"]` when the
  document is a dict, else the top-level list. Field used:
  `serialized_key`.

## Algorithm — slug resolution (this is the load-bearing part)

`mint_fillables.inventory_path_for_slug` is **not** an inventory census
and must not be used here. Resolve by file existence:

1. Strip a leading `extra/` from the bundle slug.
2. Parse both bundle slugs and inventory directory names with
   `^(.*?)-(v?)((19|20)\d\d)([a-z]?)$` → `(stem, year, suffix)`.
   Normalise `stem` by removing dashes and lowercasing.
3. Index inventory dirs by normalised stem.
4. For each bundle, take candidates sharing its stem:
   - candidates exist and one shares the **year** → `exact`
   - candidates exist, none shares the year → `skew`
   - no candidates → `absent`

Note this deliberately treats `1702ex-2018` → `1702ex-v2018c` as `exact`
(same year, differing trailing letter) and `extra/2200t-2022` →
`2200t-v2020` as `skew`.

## Algorithm — per-record classification

Emit one row per catalog record with a class. Only these are per-record
facts; do not label a record N:1, because nothing in the corpus evidences
that at record level:

| Class | Condition |
| --- | --- |
| `R1_keyed_1to1` | `official_field_key` non-empty |
| `R2_no_unique_key` | gap == `no unique fields.json key for this box` |
| `R4_mixed_cell` | gap starts `lattice mixed cell covers caption plus tin-1` |
| `R5_no_inventory` | gap == `no harvested fields.json in this checkout` **and** resolution is `absent` |
| `FALSE_NEGATIVE` | gap == `no harvested fields.json in this checkout` **and** resolution is `exact` or `skew` |
| `R2_agent_tin` | gap == `no harvested agent-TIN field_key in this checkout` |

Bundle-level residues (R3 N:1, R6 0:1, R7 growable) are **counts, not
record labels**: per bundle emit `identities`, `keyed`, `rows`, `nulls`,
`leftover_keys = max(0, rows - identities)`, `excess_identities =
max(0, identities - rows)`, and `occurrence_suffixed_keys` (keys matching
`_\d+$`).

## Acceptance criteria

The run must reproduce these exactly. **If your implementation disagrees,
the implementation is wrong — never adjust the targets to match.**

| Assertion | Value |
| --- | ---: |
| records classified | 9990 |
| `R1_keyed_1to1` | 163 |
| `R2_no_unique_key` | 6221 |
| `R2_agent_tin` | 4 |
| `R4_mixed_cell` | 1 |
| `R5_no_inventory` + `FALSE_NEGATIVE` | 3601 |
| `FALSE_NEGATIVE` | 1334 |
| bundles | 53 |
| resolution exact / skew / absent | 36 / 6 / 11 |
| inventory files / rows / null keys | 43 / 9592 / 1234 |
| keyed in bundles with inventory / without | 159 / 4 |

The 6 false-negative bundles must be exactly: `0605-1999` (61 records),
`1702ex-2018` (168), `1702mx-2018c` (503), `1702q-2018` (128),
`1702rt-2018c` (226), `extra/2200t-2022` (248).

The 6 skew bundles must be exactly: `0605-1999`, `1601-fq-2020`,
`1601eq-2019`, `1602q-2019`, `extra/1700-2018`, `extra/2200t-2022`.

Output JSON must be byte-stable across runs: sort keys, sort record rows
by `id`, sort bundle rows by slug, no timestamps inside the document
(the date lives in the filename).

## The self-test must be able to fail

`--self-test` mirrors the `field_identity.py --self-test` convention and
must include a fixture that **fails**: a synthetic record claiming an
`official_field_key` whose box does not uniquely own it. The self-test
passes only when the census rejects that record. A self-test that cannot
fail does not ship.

## Commands

```sh
python3 tools/formgen/join_census.py --self-test
python3 tools/formgen/join_census.py --tree forms-corrected \
  --out tools/formgen/corrections/evidence/join-census-20260819.json
```

`forms-corrected/` is gitignored; rebuild if absent with
`python3 tools/formgen/correct.py --batch ddac6058`. The census reads
`catalog.json` and `rules/forms/`, so `--tree` only records which tree
the evidence was taken against.

## Done when

Both commands pass, every acceptance number matches, the self-test is
demonstrated failing on the bad fixture, and the diff contains exactly
two new files. Report the numbers back before committing.

## Explicitly not in this increment

Fixing the 1334 false negatives (that is a `mint_fillables` slug-resolution
change and a catalog re-mint), G16 `comb_slots_match_printed`, C01–C07
`verified`, and any emission of `name="frm…"`.
