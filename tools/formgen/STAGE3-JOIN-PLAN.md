# Stage 3 join policy — plan

Measured 2026-08-19 on `.claude/worktrees/tin-stage2`, branch
`gol/stage3-ready`. Every number below was re-derived from
`tools/formgen/identity/catalog.json` and `rules/forms/*/fields.json` on
this tree. Mapper not started.

**Landed 2026-08-19 on `gol/join-census` (PR #24):** `join_census.py`,
the dated evidence JSON, and the remint of 1334 mint-path false
negatives. Section 3 is done.

**R1 mapper (2026-08-20, `gol/tin-map`):** `map_tin.py` copies the 163
R1 keys onto input `name=` in `forms-corrected/` only. R3/R7 stay
closed: they still have zero keyed examples.

**Headline finding: no N:1 or growable join is currently evidenced by a
single harvested key.** All 163 keyed records are 1:1 TIN suffixes. The
policy below can therefore *classify* N:1 and growable cases but cannot
yet *execute* them. R1 is the exception: 163 unique TIN keys, mapped by
`map_tin.py`. R3/R7 stay closed for lack of evidence, not missing code.

---

## 1. Join policy

A record joins on two independent facts: the **identity** (catalog `id` +
`source_printed_box_pt`) and the **key** (`serialized_key` in
`rules/forms/*/fields.json`). A join is legal only when the cardinality
case is named below *and* the key side is evidenced. Never invent a key.

### R1 — 1:1, unique harvest → key may be copied to `name=`

One identity, one `serialized_key`, harvest unique. This is the only case
that may write `name="frm…"`, and only after R6 binding holds.

> Example (real): `2550m-2007/p1/tin-1` → `frm2550m:txtTIN1`.
> All 163 keyed records today are this shape — TIN suffixes via
> `harvest_tin`. 159 sit in bundles with inventory, 4 in `2000-dst-2018`
> which has none (seeded by the C01–C07 correction path, not by harvest).

### R2 — 1:1 by position, no unique key → catalog-only annotation

Counts match but no key is uniquely attributable to this box. Leave
`name=` as the cell id; record a non-empty `official_field_key_gap`.
6221 records sit here (`no unique fields.json key for this box`).

> Example (real): `2550m-2007` — 292 identities against 292 rows, an
> exact count match, yet only 4 records are keyed. Count symmetry is not
> evidence of a join.

### R3 — N:1, many cells → one XML field → **classify, do not join**

A money comb's compartments or a growable band's rows are N HTML cells
behind one XML field. The policy names the shape; it does **not** author
a shared `name=` today, because no harvested key demonstrates the
grouping. Structurally N:1 bundles are those with more identities than
rows.

> Example (measured): `1602q-2019` — 260 identities against 110 rows
> (150 excess). `extra/1800-2018` — 237 against 177. `1604f-2018` — 180
> against 105.
> These stay gapped. The evidence that would un-gap them is a
> `fields.json` row carrying an explicit occurrence or slot index that
> resolves to a known compartment group. Do not synthesise one.

### R4 — 1:N, one cell → many keys → permanently gapped, by design

A lattice mixed cell that covers caption plus content cannot carry
several keys. It stays gapped and the sibling keys are harvested onto
their own identities.

> Example (real, verbatim gap string): `extra/1801-2018/p1/tin-strip` —
> *"lattice mixed cell covers caption plus tin-1; four HTA keys do not
> collapse onto this cell — tin-2/3/branch are separate."* Exactly one
> record in the catalog carries this gap.

### R5 — 1:0, identity with no key → annotate, ship gapped

No inventory, or inventory with nothing attributable. Leave the cell id,
record the reason. Shippable: a gapped box still prints and still tabs.

> Example (measured): `extra/1604cf-2008` — 857 identities, no
> `fields.json` anywhere in this checkout. Largest single 1:0 bundle.

### R6 — 0:1, leftover XML key → log as evidence, never mint an identity

A key with no fillable box is a measured leftover, not a defect to fix by
inventing artwork. Log it; a human decides whether it is an aggregate, a
caption-level field, or a real miss.

> Example (measured): `1701-2018` — 953 rows against 402 identities, 551
> leftover keys. `extra/2200p-2020` — 479 against 327.
> Watch two inventories that are 100% unusable: `1601eq-2019` has 621
> rows of which **621 are null**, and `1701q-2018` has 172 of which
> **172 are null**. Those are 0:1 in full — inventory present, keys
> absent.

### R7 — growable bands → occurrence indexing, and it is narrow

Repeating rows map to one field distinguished by occurrence suffix. The
corpus supports this only thinly, so treat it as a named-but-unproven
case.

> Example (real): `0605-v2003` carries 235 rows of which only **20**
> are occurrence-suffixed — `frm0605:itemFiscalStartMonth:_1`,
> `itemQuarter_1`…`itemQuarter_4`. There is no 99-row item list on 0605.
> Growable joins stay gapped until a form shows a full occurrence series
> matching a measured row count.

### R8 — binding

Stage 3 binds `forms-corrected/` only, and only where a correction
exists; never raw `forms/` behind a correction. `forms-corrected/` is
gitignored — rebuild with
`python3 tools/formgen/correct.py --batch ddac6058`.

### Which leftovers may ship

1:0 (R5) and 0:1 (R6) both ship, because both are honest and logged. What
may **never** ship is a key written onto `name=` without R1 evidence, or
an identity minted to absorb a leftover key.

---

## 2. Census (measured 2026-08-19)

### Headline

| Fact | Measured | vs handoff |
| --- | ---: | --- |
| Catalog records | 9990 | confirmed |
| Bundles | 53 | confirmed |
| Keyed (`official_field_key` set) | 163 | confirmed |
| Gapped | 9827 | confirmed |
| `fields.json` files | 43 | confirmed |
| Field rows | 9592 | confirmed |
| Null `serialized_key` | 1234 | confirmed |
| Families | text 4779, comb 1999, money 1646, date 704, xbox 654, tin 208 | confirmed |

Gap reasons: 6221 no-unique-key · 3601 no-harvested-inventory · 4
no-harvested-agent-TIN · 1 tin-strip mixed cell.

Keyed split — **159** in bundles that have inventory, **4** in
`2000-dst-2018` which has none. Total 163.

### Inventory resolution — the corrected three-way split

Resolving catalog slug → `rules/forms/*/` **by file existence**, not by
`inventory_path_for_slug`:

| Class | Bundles |
| --- | ---: |
| Exact year match | 36 |
| Year/suffix skew (inventory exists under another year) | 6 |
| No inventory anywhere | 11 |

The 6 skew bundles: `0605-1999`→`0605-v2003`, `1601-fq-2020`→`1601fq-v2018`,
`1601eq-2019`→`1601eq-v2018`, `1602q-2019`→`1602q-v2018`,
`extra/1700-2018`→`1700-v2013`, `extra/2200t-2022`→`2200t-v2020`.
`mint_fillables.SKEW` holds only 2 of these, so 4 are unhandled.

The 11 with no inventory: `0620-2019`, `1621-2019`,
`1701-2018-attachment`, `1701-2018-conso`, `1702mx-2018c-attachment`,
`1709-2020`, `2000-dst-2018`, `2316-2021`, `2550-ds-2025`, `2551m-2002`,
`extra/1604cf-2008`.

### Mint-path false negatives — 6 bundles, 1334 records

Records claiming `no harvested fields.json in this checkout` while the
inventory **exists on disk**:

| Bundle | Ids | False-neg records | Inventory | Rows | Nulls |
| --- | ---: | ---: | --- | ---: | ---: |
| `0605-1999` | 65 | 61 | `0605-v2003` | 235 | 0 |
| `1702ex-2018` | 172 | 168 | `1702ex-v2018c` | 197 | 11 |
| `1702mx-2018c` | 507 | 503 | `1702mx-v2018c` | 671 | 83 |
| `1702q-2018` | 132 | 128 | `1702q-v2018c` | 113 | 0 |
| `1702rt-2018c` | 230 | 226 | `1702rt-v2018c` | 277 | 19 |
| `extra/2200t-2022` | 252 | 248 | `2200t-v2020` | 278 | 0 |

So of the 3601 `no harvested` gaps, **1334 are false** and 2267 are
honest. Four of the six are `1702*` suffix skew (`-2018` vs `-v2018c`) —
one matcher rule recovers 1025 records.

### Per-bundle table

Full 53-row table with ids / keyed / inventory / rows / nulls / join class
is in the attached `census-table.md`. Join-class distribution: 5 bundles
0:1-heavy with >100 leftover keys, 15 structurally N:1, 11 no-inventory,
6 false-negative, 1 exact count match (`2550m-2007`).

### PLAN.md 2026-08-06 rows

| Row | Verdict |
| --- | --- |
| "13 of 53 have no `fields.json`" | **Superseded** — 11 have none; 6 more are skew, not absence. The old number was closer to truth than any gap-string count. |
| "8 joinable codes with revision skew" | **Superseded** — 6 today. |
| "0605: 71 vs 235" | **Partly confirmed** — 235 rows confirmed exactly; the identity side is **65**, not 71. |

---

## 3. Next increment (one, serial) — done 2026-08-19

**Build `tools/formgen/join_census.py` — a read-only, replayable census
that emits dated JSON evidence.** Done; see `join_census.py` and
`corrections/evidence/join-census-20260819.json`. The remint that this
section left out of scope landed in the following commit
(`join-census-20260819-remint.json`).

It resolves catalog slug → inventory by file existence (three-way: exact
/ skew / absent), classifies every one of the 9990 records into R1–R7,
and flags the false-negative set. It writes evidence only; it never
writes HTML, never touches `emit.py`, and emits no `name=` value.

Why this and not the mapper: every cardinality claim above is currently a
one-off measurement in a chat. Until it is replayable and dated, the
policy cannot be re-checked after a batch regenerates, and R3/R7 cannot
be promoted from "classified" to "joinable" on evidence.

Completion:

```sh
python3 tools/formgen/join_census.py --self-test
python3 tools/formgen/join_census.py --tree forms-corrected \
  --out tools/formgen/corrections/evidence/join-census-20260819.json
```

Passes when the run reproduces, byte-stable: 9990 classified, 163 R1,
1334 false-negative, 36/6/11 inventory split, and `--self-test` **fails**
on a fixture where a synthetic record claims a key its box does not
uniquely own. That failing fixture is the point — it proves the policy
can reject.

Files: creates `tools/formgen/join_census.py` and one evidence JSON under
the existing `corrections/evidence/` convention. Touches nothing else.
Explicit non-start: no mapper module, no `name="frm…"` in `emit.py`.

---

## 4. Still blocked

- **The mapper, R1.** Done. PR #27 landed `map_tin.py`. 2026-08-21 replay
  on `gol/windows-ebirforms-land` after the 1601EQ/1701Q/2000-dst
  inventory land: `correct.py --batch HEAD` + `--verify`, identity
  9990/9990, then `map_tin.py --write`. Pins held: R1 163, writable 148,
  unwritable-mixed 15, inputs 496, files 38. Second run idempotent
  (`files_touched` 0). Evidence:
  `corrections/evidence/tin-map-20260821.json`. Four 2200A keys
  (`tinA`/`tinB`/`tinC`/`branchCode`) stay `claimed_duplicate` in
  leftover census (occ2/occ3 are header mirrors); they stay mapped as
  R1 and are not unmapped.
- **Leftover unique keys.** Measured 2026-08-21: 8436 unclaimed keys
  appear once in their inventory. That is not a join. Do not copy them
  onto `official_field_key` or `name=` without a named harvest rule and
  a box role. Replay: `leftover_keys.py`. W5 2550M RDO/zip/address/email
  stay harvest-rule candidates, not joins.
- **The mapper, R3/R7.** Still blocked on evidence: zero keyed examples
  in the whole corpus. W4 2550M dummy Save was `other` (one money input
  + modal schedule). Do not join them.
- **The 1334 false negatives.** Reminted 2026-08-19: gap strings on the
  6 skew bundles now say “no unique key”, not “no inventory”. Catalog
  size stays 9990. No keys invented.
- **G16 `comb_slots_match_printed`** — 9 forms / 288. Untouched, out of
  scope.
- **C01–C07 `verified`.** Status is `applied`. Sitting approved
  2026-08-18; `proven: false` until `audit.py` is seen to fail on
  `forms-corrected/` for the declared divergences. Stage 2 gate, not
  Stage 3.
- **1601EQ / 1701Q / 2000-DST inventories.** Done 2026-08-21. Live
  `serialized_key` filled where dummy Save emitted the name; overlay
  snapshot `tools/formgen/inventories/2000-dst-v2018` (not `rules/forms/`).
  `claimed_absent` 0. 1601EQ still has 531 null rows the Save did not
  emit — leftover, not a join.
- **C06 agent TIN.** W7: no distinct WITHHOLDING AGENT TIN key. Item 5
  is C05. Gap stays.
- **Ten R5 bundles** still have no `fields.json`: `0620-2019`,
  `1621-2019`, `1701-2018-attachment`, `1701-2018-conso`,
  `1702mx-2018c-attachment`, `1709-2020`, `2316-2021`, `2550-ds-2025`,
  `2551m-2002`, `extra/1604cf-2008`. Gapped by design until an inventory
  exists.
