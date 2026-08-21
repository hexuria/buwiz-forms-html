# Handoff — Windows Offline eBIRForms discovery for Stage 3 leftovers

Self-contained brief for an agent on a **Windows PC that can launch
Offline eBIRForms** (`eBIRForms.exe` / `BIRForms.exe`). Everything you
need is in this file or at a path it names. Written 2026-08-20 after
PR #27 (TIN mapper) merged and the leftover-key census landed.

Your unique capability is local Save XML and the extracted installer
(`.hta`, `atcCodes.xml`, savefiles). macOS cannot produce that evidence.
Your job is **observation packets**, not a remint and not a mapper run.

---

## 0. Session setup (run first)

```text
Authoritative clone: the `bir` checkout on branch `main`
  (or `gol/leftover-keys` until PR #28 merges).
Working remote: public → hexuria/buwiz-forms.
Prefix repo commands with `rtk` when the wrapper exists.
```

Ground rules for every command in this session:

- **Dummy profile only.** TIN `000-000-000-00000`, synthetic name, no
  real taxpayer data, no live COR.
- **Local Save only.** Inspect `C:\eBIRForms\savefile\` (or the install's
  savefile directory). Do not click Submit, Send, e-mail, or any path
  that contacts BIR.
- **Final Copy / encrypted filing artifacts stay unopened** unless a job
  below names a *redacted* decrypt of an already-pinned dummy savefile.
- **Never write `official_field_key` or HTML `name=`.** That is a later
  reviewed remint + `map_tin.py`. This session produces evidence JSON
  that names keys the official client actually emitted.
- **Never point `rules/tools/build-*-package.ps1` at canonical
  `rules/forms/…`.** Those builders overwrite the snapshot they document
  (`rules/UPDATING.md`, `rules/tools/README.md`). Stage to a new
  directory.
- **Do not invent a `serialized_key`.** Copy it from save XML or from an
  HTA `saveXML` assignment you can cite by file and line.
- Push only to `public`. Never push `refs/backup/*`.

Read, in this order, only if a claim below is unclear:

1. This file.
2. `tools/formgen/leftover_keys.py` and
   `tools/formgen/corrections/evidence/leftover-keys-20260820.json`.
3. `tools/formgen/STAGE3-JOIN-PLAN.md` — R1–R8 policy. R1 is the only
   join that may write `name=`. Uniqueness in inventory is not a join.
4. `rules/tools/README.md` — most `.ps1` files are provenance records.
5. `rules/UPDATING.md` — dummy-profile Save; no online submission.

Stop there. Print-parity (`AGENTS.md` visual criterion) and the
validation-rules 43-form library are different programs. Do not apply
this handoff to them.

---

## 1. What is already done — do not redo

| PR | What | Merge on `public/main` |
| --- | --- | --- |
| #17–#20 | TIN 3-3-3-5 chrome, charboxes, x-squares | landed |
| #23 | Identity catalog 9990/9990 fillables | `868260c5` |
| #24 | Join census; remint 1334 false-negative gaps | `ef8fdc06` |
| #25–#26 | C01–C07 `verified`; re-anchor to HEAD `forms/` | `0c1def60`, `c23d9e4e` |
| #27 | `map_tin.py`: 148 writable R1 keys onto `name=` in `forms-corrected/`; 15 G11 mixed branches have no input | `9377f71c` |
| #28 | `leftover_keys.py` census (this evidence) | open or just merged |

`map_tin.py` and `leftover_keys.py --self-test` already pass. A change
that makes either fail is a regression you caused.

Replay the census (does not need eBIRForms):

```
rtk python3 tools/formgen/leftover_keys.py --self-test
rtk python3 tools/formgen/leftover_keys.py --tree forms-corrected
```

Pins you must not silently “fix”: leftover_unique **8028**,
leftover_duplicate **4**, claimed_unique **147**, claimed_duplicate **4**,
claimed_absent **12**, claimed **163**. If a job below fills
`serialized_key`s, the pins move **in the same commit** as the inventory
edit, after a live re-run.

---

## 2. Why Windows is required

`rules/` tracks JSON, `.ps1`, and markdown. It does not contain
`eBIRForms.exe`, extracted installer trees, `.hta`, `atcCodes.xml`, or
savefile XML. Those live on the Windows install, pinned by sha256 in each
form `manifest.json`.

Join Stage 3 is stuck on **missing saveXML evidence**, not missing Python:

- 8028 leftover keys are unique in `fields.json` and still unjoinable
  (no box role, and many are runtime-only).
- 12 catalog TIN keys have **no non-null `serialized_key`** in inventory.
- 4 catalog TIN keys on 2200A share one `serialized_key` three times
  (`serialized_occurrence` 1/2/3 already recorded — the boxes are not).
- R3 (N HTML cells → 1 XML field) and R7 (growable `_1`…`_N`) have
  **zero keyed examples**. A dummy Save of one money comb and one
  repeating row is the evidence that would un-gap them.

---

## 3. Jobs (serial). Finish 1 before 2.

Each job ends when the completion criterion holds. Land evidence under
`tools/formgen/corrections/evidence/` as dated JSON plus, if you touch
`fields.json`, a staging tree — not a silent edit of the live snapshot.

Use one dummy TIN everywhere: `000-000-000-00000`. Record the savefile
path and its sha256. Redact nothing if the profile is already dummy;
if a file contains anything that looks like a real TIN, stop and ask.

### Job 1 — Put `serialized_key` on 1601EQ and 1701Q

**Blocker.** Catalog claims these eight R1 TIN keys, but
`join_census` / `leftover_keys` see zero non-null `serialized_key`
rows (`claimed_absent`):

| Catalog bundle | Inventory dir | Claimed keys | Inventory |
| --- | --- | --- | --- |
| `1601eq-2019` | `rules/forms/1601eq-v2018` | `frm1601EQ:txtTIN1/2/3`, `txtBranchCode` | 621 rows, **621 omit `serialized_key`** |
| `1701q-2018` | `rules/forms/1701q-v2018` | `frm1701q:txtTIN1/2/3`, `txtBranchCode` | 172 rows, **172 omit `serialized_key`** |

`field_key` is already populated (example: `frm1601EQ:txtYear` in
`rules/forms/1601eq-v2018/fields.json`). The join reads
`serialized_key` only.

1601EQ was previously observed **without Save**
(`rules/forms/1601eq-v2018/HANDOFF.md`, `gaps.md`). A dummy Save is
what this job adds.

Steps:

1. Open Offline eBIRForms. Load **1601EQ January 2018** (runtime
   `1601eq-v2018`, package recorded in that form's `manifest.json`).
2. Fill only dummy TIN + the minimum the Save preflight demands.
3. Save. Do not Validate-for-submit, do not Final Copy, do not Submit.
4. Copy the new savefile next to a working note. Hash it.
5. List every XML element / attribute name the savefile emits, in
   document order. That list is the `serialized_key` inventory.
6. Repeat for **1701Q January 2018** (`1701q-v2018`).

Completion:

- An evidence JSON names, for each form: savefile sha256, element
  count, whether `txtTIN1` / `txtTIN2` / `txtTIN3` / `txtBranchCode`
  (or the actual emitted spelling) appear, and the exact XML snippet
  (≤20 lines) around the TIN.
- A **staging** `fields.json` (not the live file) fills `serialized_key`
  only where that savefile emitted the name. Rows the savefile did not
  emit stay without `serialized_key`.
- `leftover_keys.py` on the staging inventory shows `claimed_absent` 0
  for those two slugs **or** an explicit note that the official Save
  uses a different spelling than the catalog (quote both strings).
- You did not run `map_tin.py --write`.

### Job 2 — Give 2000-DST an inventory

**Blocker.** Catalog has four R1 keys
(`frm2000:txtTIN1/2/3`, `frm2000:txtBranchCode`) on bundle
`2000-dst-2018`. There is **no** `rules/forms/2000*` directory.
Those four keys were seeded by the C01–C07 path, not by harvest.

Steps:

1. Identify the exact Offline eBIRForms form: code, printed revision,
   package version. Record them. Do not guess `2000` vs `2000-DST`.
2. Dummy-fill TIN. Save.
3. Stage `rules/forms/<new-snapshot>/fields.json` from saveXML names
   **or** from the HTA `saveXML` routine cited by file+line. Do not
   copy 2000-OT / 2200 keys.

Completion:

- Staging tree exists; live `rules/forms/` is untouched.
- Evidence JSON: package version, HTA path+sha256, savefile sha256,
  whether the four catalog keys appear verbatim.
- If the official keys differ in spelling, quote both. Do not retcon
  the catalog in this job.

### Job 3 — Disambiguate 2200A TIN occurrences

**Blocker.** `extra/2200a-2020` ships four R1 keys that each appear
**three times** in `rules/forms/2200a-v2020/fields.json`
(`claimed_duplicate`). The inventory already stored the split:

| `field_key` | `serialized_key` | `serialized_occurrence` | HTA control |
| --- | --- | ---: | --- |
| `frm2200Av2020:tinA` | `frm2200Av2020:tinA` | 1 | `official-hta-runtime#control:L396` |
| `frm2200Av2020:tinA#occurrence-2` | same | 2 | `L1390` |
| `frm2200Av2020:tinA#occurrence-3` | same | 3 | `L1943` |

Same pattern for `tinB`, `tinC`, `branchCode`. Notes already say
“Duplicate serialized key occurrence preserved losslessly in DOM
order.” What is missing is **which printed box** each occurrence is.

Steps:

1. Open 2200-A January 2020 in eBIRForms.
2. For HTA lines L396, L1390, L1943 (and the matching B/C/branch
   controls beside them), record: on-screen caption, page, item
   number, whether the box is taxpayer TIN, a schedule TIN, or a
   related-party TIN.
3. Dummy-type a distinct digit into occurrence 1 only, Save, and
   show which XML node changed. Repeat for 2 and 3 if they are
   writable without Submit.

Completion:

- A table: occurrence → caption → page/item → control line →
  writable-on-Save (yes/no) → catalog identity candidate *if obvious*
  (leave blank if not). Identity ids look like
  `extra/2200a-2020/p1/tin-1`.
- You did not change `harvest_tin` or the catalog. The table is the
  deliverable. A remint that keeps occurrence 1 as R1 and gaps 2/3
  (or maps three identities) is a **later** PR.

### Job 4 — One money comb and one growable band (R3 / R7)

**Blocker.** R3 and R7 are named in `STAGE3-JOIN-PLAN.md` and have
zero keyed examples. Do not join them. Produce the saveXML shape.

Pick **one** form you can open, preferably `2550M February 2007`
(bundle `2550m-2007`, inventory `2550m-v2007` — exact count match,
292 identities / 292 rows, only 4 TIN keys today) or `0605` (20
occurrence-suffixed keys already: `itemFiscalStartMonth:_1`,
`itemQuarter_1`…`_4`).

Steps:

1. Dummy TIN. Type one amount into a printed money comb (several
   digit cells, one amount). Save. Note XML: one node or N nodes?
2. If the form has a repeating schedule/band, add two rows with
   different dummy values. Save. Note XML: `…_1` / `…_2` or one
   list or something else.

Completion:

- Evidence JSON with the two savefile hashes and the XML node names
  for that money field and those two rows, quoted.
- A one-line verdict: `R3-shaped` (N cells, 1 key), `R7-shaped`
  (occurrence suffix matching row index), `other` (quote the shape).
- No catalog edits.

### Job 5 — Classify leftover leaves on 2550M

**Not a join.** 2550M has **246** leftover_unique keys. Across the
corpus the leftover-unique leaf histogram includes both
taxpayer-looking names and runtime names:

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

1. Dummy Save of 2550M with TIN, RDO, ZIP, address, email, taxpayer
   name filled if those boxes exist and Save allows it.
2. Partition the 246 leftover keys into: **emitted** (present in the
   savefile) vs **silent** (inventory-only / hidden).
3. For each emitted leaf in the candidate row of the table, name the
   printed box (caption + approx position). If two boxes could own
   it, write `not unique` — that is a successful observation.

Completion:

- Evidence JSON: savefile sha256, `emitted` list, `silent` list
  (runtime leaves may be a prefix of silent).
- A harvest-rule proposal **only** for a leaf that is (a) emitted,
  (b) the only leftover key in that bundle with that leaf, (c) bound
  to one named printed box. Proposal is text in the JSON, not a code
  change.

### Job 6 — The four leftover_duplicate keys

Unclaimed keys with inventory count > 1. Same method as Job 3:

| Bundle | Key | Count |
| --- | --- | ---: |
| `1702q-2018` | `frm1702q:txtTelNum` | 2 |
| `1707a-2021` | `frm1707Av2021:txtI11Email` | 2 |
| `2551q-2018` | `txtEmail` | 2 |
| `extra/2200a-2020` | `frm2200Av2020:registeredName` | 3 |

Completion: for each key, caption/page/item per occurrence, or
“one control serialized twice” with the HTA lines. No remint.

### Job 7 — 1600WP agent TIN (C06) only if Jobs 1–3 are done

C06 widened the **WITHHOLDING AGENT TIN** branch on
`extra/1600wp-2010`. Catalog gap:
`no harvested agent-TIN field_key in this checkout`. Inventory
`frm1600WP:txtBranchCode` is item 5 (C05), not this comb.

Completion: HTA control id for that header comb, or a quoted finding
that Save does not emit a distinct key (then the gap stays).

---

## 4. How to read a Save file

1. After Save, copy the file out of `C:\eBIRForms\savefile\` (name
   often `00000000000000-<form>-*.xml`).
2. If it is plaintext XML, list element names with a small script;
   do not commit the savefile (it is gitignored and must stay dummy).
3. If it is encrypted, `rules/tools/extract-encrypted-field-keys.ps1`
   is the provenance decryptor. It needs the ciphertext path, a
   **redacted** output path, form id, and sha256 pins. Run it in
   `-Discovery` only if this checkout has no pins yet; then pin.
   Do not commit decrypted real-taxpayer XML. Dummy only.
4. Compare JSON with `jq -S -c`, not `diff`. Windows PowerShell 5.1
   and pwsh 7 emit different `ConvertTo-Json` bytes for the same
   object (`rules/tools/README.md`).

`rules/tools/*.ps1` package builders default to one developer's
absolute paths and write into historical `rules/forms/…`. Read
`README.md` in that directory before invoking any of them. Prefer
hand-built staging JSON over re-running a builder.

---

## 5. How to land (when Uriah says land)

1. Evidence-only commit first: dated JSON under
   `tools/formgen/corrections/evidence/`, dummy savefile hashes,
   no PDFs, no savefiles, no `.hta`.
2. If a `fields.json` must change, copy the snapshot to a **new**
   staging dir, edit there, show `leftover_keys.py` before/after
   counts. Live pin updates ride in that same commit.
3. Catalog remint and `map_tin.py --write` are **out of scope**
   until a named harvest rule exists and is reviewed. R3/R7 stay
   classified, not joined, even after Job 4.
4. Open a PR to `hexuria/buwiz-forms` as `hexuria`. Do not merge
   unless Uriah says merge.

---

## 6. Return format (paste back)

```text
JOB n: <title>
  SAVEFILE: <path> sha256=<64 hex>
  FORM: <official code> <printed revision> package=<version>
  FINDING: <one paragraph, keys quoted verbatim>
  STAGING: <path or "none">
  PINS: leftover_keys.py <unchanged | new counts>
  NEXT: <job number or stop>
```

If a job is blocked on a dialog that looks like Submit, stop that job
and return the dialog text instead of clicking.

---

## 7. Out of scope (different programs)

- Print-pixel chasing, comb-referee, G16 `comb_slots_match_printed`.
- Promoting 2550Q v2 (`bir-core` / `bir-rules` production guards).
- Desktop / GPUI / filing queue.
- Inventing HTML checkboxes; x-squares stay `type=text maxlength=1`.
- Regenerating `forms/` or re-anchoring C01–C07.
```
