# Field identity

A **field identity** is a name for one taxpayer-facing box that still means
that box after the lattice renumbers `p1c13`. It is not a bbox string and it
is not an HTML `id`. Stage 3 will join identities to official
`serialized_key` values. This directory is not that join.

    STAGE 1  GENERATE   forms/          (batch-versioned; `p1cN` may move)
    STAGE 2  CORRECT    forms-corrected/
    IDENTITY            this catalog    (durable id + printed box)
    STAGE 3  MAP        identity → eBIRForms XML key   (R1 TIN: rebuild+verify forms-corrected, then map_tin.py --write)

## What is frozen

Each record's `id` (for example `2550m-2007/p1/tin-branch`) never changes
once published. The artwork identity is `source_printed_box_pt`, measured
from the pinned PDF. `html_id_hint` is the current batch's `p1cN` and is
non-authoritative: a geometry fix that renumbers cells must update the hint
in the same commit (a schema change), but it must not mint a new identity.

## How a record is resolved

`field_identity.py` parses the named tree with the stdlib HTML parser — not
`emit.py`, not `lattice.py`. It collects fillable boxes: `data-cell-kind` is
`field` *or* `mixed`, and `data-field-kind` is set (comb or text). C01's
first TIN group and C06's agent TIN emit as `text` on the stage-1 batch.
G11 mixed combs are the branch identity when the sheet pre-prints `000` and
emit refuses empty slots. A white knockout covering the strip is
`data-cell-kind="blank"` and is ignored. Dash separators are `data-cell-kind=
"field"` with no `data-field-kind`; they are ignored too, because the even
reflow parks their centers inside the previous group's printed box.

Match is **center-in-printed-box**, not raw overlap. Stage 2 even-3-3-3-5
reflow expands the branch left, so neighbouring groups nick each other's
old edges by a fraction of a point. The emitted field whose center still
sits in `source_printed_box_pt` (tolerance `match.tolerance_pt`) is the
same subject. Exactly one such hit, whose `id` equals `html_id_hint`, is
success.

| Result | Meaning |
| --- | --- |
| resolved | exactly one field center in the printed box, and its `id` equals `html_id_hint` |
| html_id_hint_stale | unique center hit, different `id` — update the catalog in this commit |
| unresolved | no field center sits in the printed box |
| ambiguous | two or more field centers sit in the printed box |

Zero or two is a failure. A stale hint is also a failure: silent remapping
is risk R2. The identity id still names the same box; only the hint moves.

## Coverage

9990 identities on both `forms/` and `forms-corrected/` — every fillable
cell. C01–C07 seed (28) + I0 TIN caption chains (152) + I1 TIN leftovers
(28) + I2 combs (4349) + I2 x-squares (654) + I2 wide text (4779). Wide
text is `text-*`. Evidence:
[`identity-text-20260818.json`](../corrections/evidence/identity-text-20260818.json).

`coverage --tree forms` and `coverage --tree forms-corrected` report
9990/9990. I3 pins that 0-gap in `field_identity.py` (`EXPECTED_FILLABLE_CELLS`)
and `gate.py` (`EXPECTED_UNCATALOGUED_FILLABLES`). Remainder mint was 0:
odd-size text went into I2 `text-*`. Evidence:
[`identity-remainder-20260818.json`](../corrections/evidence/identity-remainder-20260818.json).

## What this is not

- Not Stage 3 itself. `map_tin.py` is the R1 TIN mapper and is the only
  thing that writes `name="frm2550m:txtBranchCode"`, and only after
  `correct.py --batch HEAD` plus `--verify` on the unmapped tree.
- Not verification of C01–C07. Overlap does not re-derive `expected_effect`.

```sh
python3 tools/formgen/field_identity.py --self-test
python3 tools/formgen/field_identity.py check --tree forms-corrected
python3 tools/formgen/field_identity.py check --tree forms
python3 tools/formgen/field_identity.py coverage --tree forms
python3 tools/formgen/field_identity.py coverage --tree forms-corrected
python3 tools/formgen/field_identity.py ledger-check --tree forms
python3 tools/formgen/field_identity.py ledger-rewrite --tree forms --write
```

`ledger-check` requires every `pXcN` in `where`/`what` to exist as a live
HTML element id (fillable or not) or a catalog id that resolves. Fillable
subjects were rewritten to catalog ids; dead `p1cN` became `former_p1cN`.
Non-fillable ink (triangles, labels) keeps a current cell id and is not
minted as a fillable identity.
