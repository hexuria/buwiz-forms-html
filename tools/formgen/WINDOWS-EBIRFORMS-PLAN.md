# Plan — Windows Offline eBIRForms observation packets (Stage 3 leftovers)

Executable plan derived from
[HANDOFF-WINDOWS-EBIRFORMS.md](HANDOFF-WINDOWS-EBIRFORMS.md). Written 2026-08-20
on `public/main` at `df537eb` (PR #28 merged). Policy stays in the handoff and
[STAGE3-JOIN-PLAN.md](STAGE3-JOIN-PLAN.md). This file is the serial queue, the
machine pins, and the done-when checks.

This is **not** a remint, **not** a mapper run, and **not** print-parity.
The deliverable is dated evidence JSON under
`tools/formgen/corrections/evidence/`. R1 is still the only join that may write
`name=`, and that write is out of scope here.

---

## Start as `/goal`

Slash-goal objectives are the first prompt and the completion criteria, and they
cap at 4,000 characters. Paste the prompt from
[GOAL-WINDOWS-EBIRFORMS.md](GOAL-WINDOWS-EBIRFORMS.md) (the fenced `text` block
at the top). Codex should drive Computer Use, dummy-Save, staging, evidence,
verification, and W0→W8 without job-to-job babysitting. Land is evidence-first
PR, no merge.

---

## 0. Objective

Unblock Stage 3 leftovers that Python cannot see: saveXML names, occurrence
boxes, R3/R7 shapes, leftover-leaf classification. macOS cannot produce this
evidence. Windows can, because it can launch Offline eBIRForms and inspect
dummy Save XML plus the extracted `.hta`.

Done when Jobs 1–6 have observation packets (Job 7 only after 1–3), each
packet uses the return format in §8, and no live snapshot /
`official_field_key` / HTML `name=` was written.

---

## 1. Machine state (do not skip)

Checked 2026-08-20 on this PC before the first Save.

| Fact | Measured |
| --- | --- |
| Checkout | `C:\Users\uriah\Code\buwiz-forms`, branch `main`, `df537eb` = PR #28 |
| Remote | `origin` → `git@github.com:hexuria/buwiz-forms.git` (public) |
| `rtk` wrapper | **missing** — run `python` / `python3` directly |
| `forms-corrected/` | **absent** — leftover census does not read HTML; `--tree` is a recorded binding only |
| Install | `C:\eBIRForms\BIRForms.exe` product **7.9.6.1** |
| Live exe | 58,411,008 bytes, sha256 `a43a4599f95158e6ba0e7a1c4b88c4e2cf215ac86e53c24259cc69d1b664829c` |
| Manifest pin (stale vs live) | 7.9.6.0, 57,506,304 bytes, sha256 `de8ef0815509d65189e6794e1f8135a5ecf5f2800005d1fc5c87043efd96dbca` |
| Extracted HTA root (this boot) | `C:\Users\uriah\AppData\Local\Temp\{0B33C1CE-21A8-44A1-8D91-28A10444A6A3}\` |
| Savefile dir | `C:\eBIRForms\savefile\` empty except `readme.txt` — prior dummy saves named in manifests are gone |
| Profile dir | `C:\eBIRForms\profile\` empty except `readme.txt` / `.gitkeep` |

HTA hashes that still match the 7.9.6.0 form manifests (re-hash after every
package restart; the GUID path is ephemeral):

| Runtime | File | sha256 |
| --- | --- | --- |
| 1601EQ Jan 2018 | `forms\BIR-Form1601EQ.hta` | `cd56bf18d1da2127d578af611fe8005fe49913a65781bb603b22b31bbe548b96` |
| 1701Q Jan 2018 | `forms\BIR-Form1701Qv2018.hta` | `5f164dde6154b96f28e23656ed2ef29406010ee3f94333e88ea6eb107fe589a0` |
| 2000 Jan 2018 DST | `forms\BIR-Form2000v2018.hta` | `43aa6fcdba1ffb40bebb3dc1e8509a87b84799b38937bb6bc57273e9021c4a7b` |
| 2000 legacy (exclude) | `forms\BIR-Form2000.hta` | `8b80e909f7f70785dbdf002488b2c14b0a4a2fca7b3c783b68cdd5a59f3ca475` |
| 2200A Jan 2020 | `forms\BIR-Form2200Av2020.hta` | `1df302eeb1352eccb88f6aa7a23fdcc185b6fbb4d15435996250f985a0198e2c` |
| 2550M Feb 2007 | `forms\BIR-Form2550M.hta` | `72f1422dab2f8523d140aa51fe5f54f7d9025acc2cf877b37c8d92b60c7668b5` |

Package 7.9.6.1 vs pinned 7.9.6.0 is **discovery context, not a silent
upgrade of `rules/`**. Record both hashes on every evidence JSON. Do not
rewrite a 7.9.6.0 snapshot in place. If an HTA hash diverges from the
manifest, quote both and keep going on observation; do not retcon the
manifest in this plan.

Leftover pins (must not silently “fix”). Replay:

```text
python tools/formgen/leftover_keys.py --self-test
python tools/formgen/leftover_keys.py --tree forms-corrected
```

| Pin | Value |
| --- | ---: |
| leftover_unique | 8028 |
| leftover_duplicate | 4 |
| claimed_unique | 147 |
| claimed_duplicate | 4 |
| claimed_absent | 12 |
| claimed | 163 |

`leftover_keys.py --self-test` already passes on this tree. A change that
makes that self-test or `map_tin.py` fail is a regression this session caused.

`leftover_keys.py` pins the **whole corpus**. A staging tree that fills
`serialized_key` will make the CLI exit 1 until `ACCEPTANCE` moves, and it
does not write JSON on failure. For Jobs 1–2, copy the full `rules/forms`
tree into staging, edit only the named snapshots, then inspect those two
bundle rows via `join_census` / `leftover_keys.build_census` (or read the
FAIL lines). Do not expect the pinned CLI to exit 0 until the land commit
updates pins in the same change as the live inventory.

Live `rules/forms/` stays untouched until Uriah says land.

---

## 2. Ground rules (every command)

- Dummy profile only. TIN `000-000-000-00000`, synthetic name, no real
  taxpayer data, no live COR.
- Local Save only. Inspect `C:\eBIRForms\savefile\`. Never click Submit,
  Send, e-mail, Validate-for-submit, or Final Copy.
- Final Copy / encrypted filing artifacts stay unopened unless a job names a
  *redacted* decrypt of an already-pinned dummy savefile.
- Never write `official_field_key` or HTML `name=`. Never run
  `map_tin.py --write`.
- Never invent a `serialized_key`. Copy it from save XML or from an HTA
  `saveXML` assignment cited by file and line.
- Never point `rules/tools/build-*-package.ps1` at canonical
  `rules/forms/…`. Stage to a new directory.
- Push only to `public`. Never push `refs/backup/*`. Land only when Uriah
  says land (§7).
- If a dialog looks like Submit, stop that job and return the dialog text.

Read, only if a claim below is unclear, in this order:

1. [HANDOFF-WINDOWS-EBIRFORMS.md](HANDOFF-WINDOWS-EBIRFORMS.md)
2. `tools/formgen/leftover_keys.py` and
   `tools/formgen/corrections/evidence/leftover-keys-20260820.json`
3. [STAGE3-JOIN-PLAN.md](STAGE3-JOIN-PLAN.md) — R1–R8. R1 is the only join
   that may write `name=`. Uniqueness in inventory is not a join.
4. `rules/tools/README.md` — most `.ps1` files are provenance records.
5. `rules/UPDATING.md` — dummy-profile Save; no online submission.

Stop there. Print-parity and the validation-rules 43-form library are
different programs.

---

## 3. Why these jobs, in this order

Join Stage 3 is stuck on **missing saveXML evidence**, not missing Python:

| Blocker | Why Windows |
| --- | --- |
| 12 `claimed_absent` TIN keys | 1601EQ and 1701Q inventories have `field_key` but **zero** non-null `serialized_key` (621/621 and 172/172). 1601EQ was last observed **without Save**. 1701Q’s old dummy save is gone from disk. |
| 4 catalog keys on `2000-dst-2018` | Catalog slug has **no** inventory under that name. `join_census` self-test: `2000-dst-2018 does not steal 2000-v2018`. |
| 4 `claimed_duplicate` TIN keys on 2200A | Same `serialized_key` three times; boxes are not identified. |
| R3 / R7 | Zero keyed examples in the whole corpus. Classify shape from one money comb and one repeating band; do not join. |
| 8028 leftover_unique | Inventory facts. 2550M alone has 246. Partition emitted vs silent before anyone proposes a harvest rule. |
| 4 leftover_duplicate | Same method as 2200A occurrences. |
| 1600WP agent TIN (C06) | Catalog gap `no harvested agent-TIN field_key in this checkout` on `tin-agent-*`. Only after Jobs 1–3. |

Jobs are **serial**. Finish *n* before *n+1*.

---

## 4. Handoff claims that are stale on this PC

Keep these visible so an agent does not “correct” the census by accident.

1. **PR #28 is merged.** Handoff said “open or just merged”. HEAD is
   `df537eb` Census leftover serialized_keys… (#28).
2. **Package is 7.9.6.1**, not the 7.9.6.0 the form manifests pin. HTA
   hashes above still match; the exe hash does not.
3. **Job 2’s “no `rules/forms/2000*` directory” is false.**
   `rules/forms/2000-v2018` and `rules/forms/2000ot-v2018` exist.
   `2000-v2018/fields.json` already has `frm2000:txtTIN1/2/3` and
   `frm2000:txtBranchCode` with non-null `serialized_key`. Catalog bundle
   `2000-dst-2018` is still `resolution: absent` **by design**
   (`join_census.resolve_slug` will not bind `2000-dst-*` to
   `2000-v2018`). Job 2 must not retcon that matcher and must not copy
   2000-OT keys.
4. **Job 3 line L1943 is stale.** Live 2200A HTA occurrences:

| occ | `tinA` line | `name=` | writable in HTA |
| ---: | ---: | --- | --- |
| 1 | 396 | `frm2200Av2020:tinA` | yes |
| 2 | 1390 | `frm2200Av2020:tinA_2` | `disabled` |
| 3 | **2007** (not 1943) | `frm2200Av2020:tinA_3` | `disabled` |

   Same pattern for `tinB` / `tinC` / `branchCode` / `registeredName`
   (`_2` / `_3` names, shared `id`). L1943 is now an unrelated schedule
   amount. Cite live lines; do not hunt for 1943.
5. **Prior dummy savefiles named in manifests are gone.** Job 1 and Jobs 4–5
   must create new Saves and hash them. Do not commit the XML.

---

## 5. Jobs

Use TIN `000-000-000-00000` everywhere. After each Save: copy the file out
of `C:\eBIRForms\savefile\` immediately (typical name
`00000000000000-<form>-*.xml`), hash sha256, list element/attribute names
in document order. The client can overwrite on the next navigation. Do not
commit the savefile. If anything looks like a real TIN, stop and ask.

If the savefile is encrypted, `rules/tools/extract-encrypted-field-keys.ps1`
is the provenance decryptor. Run it in `-Discovery` only when this checkout
has no pins yet; dummy only; never commit decrypted XML. Prefer plaintext
Save.

Compare JSON with `jq -S -c`, not `diff` (Windows PowerShell 5.1 vs pwsh 7
emit different `ConvertTo-Json` bytes).

Staging root for any `fields.json` edit:
`tools/formgen/corrections/staging/<job>-<yyyymmdd>/` — never
`rules/forms/<live>/`. Copy the full `rules/forms` tree into that staging
root before editing, so later pin math still has 43 inventories.

### Job 1 — Put `serialized_key` on 1601EQ and 1701Q

**Blocker.** Eight R1 TIN keys are `claimed_absent`:

| Catalog | Inventory | Claimed keys | Today |
| --- | --- | --- | --- |
| `1601eq-2019` (skew → `1601eq-v2018`) | 621 rows | `frm1601EQ:txtTIN1/2/3`, `txtBranchCode` | **621 omit `serialized_key`** |
| `1701q-2018` (exact) | 172 rows | `frm1701q:txtTIN1/2/3`, `txtBranchCode` | **172 omit `serialized_key`** |

`field_key` is already populated. The join reads `serialized_key` only.
Spouse / page-2 TIN keys on 1701Q are **not** this job.

Steps:

1. Open Offline eBIRForms. Load **1601EQ January 2018** (runtime
   `1601eq-v2018`). Confirm HTA hash against §1.
2. Fill only dummy TIN + the minimum Save preflight demands.
3. Save. Do not Validate-for-submit / Final Copy / Submit.
4. Copy savefile, hash it, list every XML name in document order.
5. Repeat for **1701Q January 2018** (`1701Qv2018`, not the older
   `BIR-Form1701Q.hta`).

Completion:

- Evidence JSON per form: savefile path + sha256, package 7.9.6.1 vs
  manifest 7.9.6.0, HTA path+sha256, element count, whether
  `txtTIN1/2/3` / `txtBranchCode` (or the actual spelling) appear, XML
  snippet ≤20 lines around the TIN.
- Staging `fields.json` fills `serialized_key` **only** where that
  savefile emitted the name. Rows not emitted stay without
  `serialized_key`.
- Census on the staging tree shows `claimed_absent` 0 for those two slugs
  **or** an explicit note that official Save uses a different spelling
  (quote both).
- No `map_tin.py --write`.

### Job 2 — Give 2000-DST an inventory the catalog can see

**Blocker.** Catalog has four R1 keys on bundle `2000-dst-2018` and
`join_census` resolves that slug as **absent**. Do not steal `2000-v2018`.

Live form to open: **BIR Form 2000v2018** (Documentary Stamp), HTA
`BIR-Form2000v2018.hta`, `APPLICATIONNAME="2000v2018"`. Exclude
`BIR-Form2000.hta` (legacy) and `2000OT`.

Existing `rules/forms/2000-v2018` already serializes those four keys.
That does **not** complete this job: the catalog slug still has no
inventory file of its own, and the matcher is pinned not to bind them.

Steps:

1. Record official code, printed revision, package version, HTA hash.
   Do not guess `2000` vs `2000-DST` — quote the window title and
   printed header.
2. Dummy-fill TIN. Save. Hash.
3. Stage `rules/forms/<new-snapshot>/` from saveXML names **or** HTA
   `saveXML` cited by file+line. Snapshot id must be a **new** directory
   (example shape: `2000-dst-v2018` or a package-qualified id). Do not
   edit live `2000-v2018` / `2000ot-v2018`. Do not change
   `join_census.SLUG_RE` / `resolve_slug` in this job.

A later land of a real `2000-dst-*` snapshot is allowed; that is not the
same as letting `2000-dst-2018` steal `2000-v2018`. If land happens, the
`join_census` self-test that currently expects `kind == absent` on live
inventories must be updated in that same commit so it still forbids the
steal, not the new snapshot.

Completion:

- Staging tree exists; live `rules/forms/` untouched.
- Evidence JSON: package version, HTA path+sha256, savefile sha256,
  whether the four catalog keys appear verbatim, and the exact reason
  `2000-dst-2018` still does or does not resolve.
- If official keys differ in spelling, quote both. Do not retcon the
  catalog.

### Job 3 — Disambiguate 2200A TIN occurrences

**Blocker.** `extra/2200a-2020` ships four R1 keys that each appear three
times in `rules/forms/2200a-v2020/fields.json` (`claimed_duplicate`).
Inventory already stored the split (`serialized_occurrence` 1/2/3).
Missing: **which printed box**.

Use **live** HTA lines from §4, not the handoff’s L1943.

Steps:

1. Open 2200-A January 2020.
2. For occ 1/2/3 of `tinA/B/C` + `branchCode`, record caption, page,
   item number, taxpayer vs schedule vs related-party TIN, and whether
   the control is disabled.
3. Dummy-type a distinct digit into occurrence 1 only, Save, show which
   XML node changed. Repeat for 2 and 3 **only if writable without
   Submit**. Disabled copies that mirror occ 1 are a valid finding —
   write that down; do not enable them.

Completion: a table occurrence → caption → page/item → control line →
`name=` → writable-on-Save → catalog identity candidate *if obvious*
(`extra/2200a-2020/p1/tin-1` style). Leave candidate blank if not
obvious. No catalog / `harvest_tin` edit. Remint that keeps occ 1 as R1
and gaps 2/3 is a **later** PR.

### Job 4 — One money comb and one growable band (R3 / R7)

**Blocker.** R3 and R7 have zero keyed examples. Do not join them.
Produce the saveXML shape.

Preferred form: **2550M February 2007** (exact count match, 292/292, only
4 TIN keys). Alternative: **0605** (already has 20 occurrence-suffixed
keys: `itemFiscalStartMonth:_1`, `itemQuarter_1`…`_4`).

Steps:

1. Dummy TIN. Type one amount into a printed money comb (several digit
   cells, one amount). Save. One XML node or N nodes?
2. If a repeating schedule/band exists, add two rows with different dummy
   values. Save. `…_1` / `…_2`, a list, or other?

Completion:

- Evidence JSON with the two savefile hashes and quoted XML node names.
- One-line verdict: `R3-shaped` (N cells, 1 key), `R7-shaped`
  (occurrence suffix matching row index), or `other` (quote the shape).
- No catalog edits. R3/R7 stay classified, not joined.

### Job 5 — Classify leftover leaves on 2550M

**Not a join.** 2550M has **246** leftover_unique keys.

| Leaf | leftover_unique bundles | Treat as until proven otherwise |
| --- | ---: | --- |
| `txtFinalFlag` | 34 | runtime / filing-state |
| `ebirOnlineUsername` / `ebirOnlineSecret` / `ebirOnlineConfirmUsername` | 34 each | runtime / login |
| `txtEnroll` / `driveSelectTPExport` | 34 | runtime |
| `txtCurrentPage` / `txtMaxPage` | 30 | runtime |
| `txtEmail` | 26 | candidate only if Save emits it and one box owns it |
| `txtRDOCode` | 20 | candidate only if … |
| `txtZipCode` | 17 | candidate only if … |
| `txtAddress` | 16 | candidate only if … |
| `txtTaxpayerName` | 10 | candidate only if … |

Steps:

1. Dummy Save of 2550M with TIN, RDO, ZIP, address, email, taxpayer name
   filled if those boxes exist and Save allows it.
2. Partition the 246 leftover keys into **emitted** vs **silent**.
3. For each emitted leaf in the candidate row, name the printed box. If
   two boxes could own it, write `not unique` — that is a successful
   observation.

Completion:

- Evidence JSON: savefile sha256, `emitted` list, `silent` list
  (runtime leaves may be a prefix of silent).
- Harvest-rule **proposal** only for a leaf that is (a) emitted, (b) the
  only leftover key in that bundle with that leaf, (c) bound to one named
  printed box. Proposal is text in the JSON, not a code change.

### Job 6 — The four leftover_duplicate keys

Same method as Job 3. No remint.

| Bundle | Key | Count | Inventory |
| --- | --- | ---: | --- |
| `1702q-2018` | `frm1702q:txtTelNum` | 2 | `1702q-v2018c` |
| `1707a-2021` | `frm1707Av2021:txtI11Email` | 2 | `1707a-v2021` |
| `2551q-2018` | `txtEmail` | 2 | `2551q-v2018` |
| `extra/2200a-2020` | `frm2200Av2020:registeredName` | 3 | `2200a-v2020` (HTA L439 / L1396 / L2013) |

Completion: caption/page/item per occurrence, or “one control serialized
twice” with the HTA lines.

### Job 7 — 1600WP agent TIN (C06), only if Jobs 1–3 are done

C06 widened the **WITHHOLDING AGENT TIN** branch on `extra/1600wp-2010`.
Catalog ids `tin-agent-1/2/3/branch` are gapped
`no harvested agent-TIN field_key in this checkout`. Inventory
`frm1600WP:txtTIN1/2/3` + `txtBranchCode` are item **5** (C05), not this
comb. Schedule payee TINs `dtSched:txtTin1`…`10` are a different field.

Completion: HTA control id for that header comb, or a quoted finding that
Save does not emit a distinct key (then the gap stays).

---

## 6. How to read a Save file

1. Copy out of `C:\eBIRForms\savefile\` immediately; the client may
   overwrite on the next navigation (1601EQ handoff already recorded a
   stale 1701Q save from a wrong menu index).
2. Plaintext XML: list names with a small script; do not commit.
3. Encrypted: provenance decryptor only, dummy only, redacted output path,
   pin sha256.
4. Evidence JSON names keys the official client **actually emitted**.

---

## 7. How to land (only when Uriah says land)

1. Evidence-only commit first: dated JSON under
   `tools/formgen/corrections/evidence/`, dummy savefile hashes, no PDFs,
   no savefiles, no `.hta`.
2. If a `fields.json` must change, copy the snapshot to a **new** staging
   dir, edit there, show leftover-key class counts before/after. Live pin
   updates ride in that same commit.
3. Catalog remint and `map_tin.py --write` stay out of scope until a named
   harvest rule exists and is reviewed. R3/R7 stay classified.
4. Open a PR to `hexuria/buwiz-forms` as `hexuria`. Do not merge unless
   Uriah says merge.

Suggested branch when landing is authorised: `gol/windows-ebirforms-obs`.
Do not fold this into PRs #17–#28.

---

## 8. Return format (paste back after every job)

```text
JOB n: <title>
  SAVEFILE: <path> sha256=<64 hex>
  FORM: <official code> <printed revision> package=<version>
  HTA: <path> sha256=<64 hex>
  EXE: 7.9.6.1 sha256=a43a4599… (vs manifest 7.9.6.0 de8ef081…)
  FINDING: <one paragraph, keys quoted verbatim>
  STAGING: <path or "none">
  PINS: leftover_keys.py <unchanged | new counts>
  NEXT: <job number or stop>
```

---

## 9. Out of scope

- Print-pixel chasing, comb-referee, G16 `comb_slots_match_printed`.
- Promoting 2550Q v2 (`bir-core` / `bir-rules` production guards).
- Desktop / GPUI / filing queue.
- Inventing HTML checkboxes; x-squares stay `type=text maxlength=1`.
- Regenerating `forms/` or re-anchoring C01–C07.
- Changing `join_census` so `2000-dst-2018` steals `2000-v2018`.
- Any Submit / Final Copy / live COR / real TIN.

---

## 10. Queue (serial)

| # | Job | Writes | Parallel? |
| --- | --- | --- | --- |
| W0 | Session setup + leftover pin replay + record 7.9.6.1 hash drift | evidence note only | first |
| W1 | 1601EQ + 1701Q dummy Save → staging `serialized_key` | staging `fields.json` + evidence JSON | after W0 |
| W2 | 2000-DST identity + dummy Save → new staging snapshot | new staging dir + evidence JSON | after W1 |
| W3 | 2200A occurrence → printed box table | evidence JSON | after W2 |
| W4 | 2550M (or 0605) money comb + growable band | evidence JSON | after W3 |
| W5 | 2550M leftover leaf emitted/silent split | evidence JSON | after W4 |
| W6 | four leftover_duplicate keys | evidence JSON | after W5 |
| W7 | 1600WP agent TIN | evidence JSON | after W1–W3 |
| W8 | land | PR, no merge | only if Uriah says land |
