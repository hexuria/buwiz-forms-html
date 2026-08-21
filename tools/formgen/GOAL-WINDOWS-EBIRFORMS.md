# Goal runner — Windows Offline eBIRForms observation packets

Paste this into `/goal` (≤4000 chars). This file is the long runner the
objective points at.

```text
Execute tools/formgen/WINDOWS-EBIRFORMS-PLAN.md jobs W0–W8 on this Windows PC as the sole operator. You drive Offline eBIRForms, dummy-Save, hash, parse XML, stage inventories, verify, write evidence, and start the next job without waiting for me.

Outcome: dated observation packets that unblock Stage 3 leftovers Python cannot see (saveXML names, 2200A boxes, R3/R7 shape, leftover leaves). Not a remint, not a mapper, not print-parity.

Read first, in order, and obey: tools/formgen/HANDOFF-WINDOWS-EBIRFORMS.md; tools/formgen/WINDOWS-EBIRFORMS-PLAN.md; tools/formgen/GOAL-WINDOWS-EBIRFORMS.md. Policy: tools/formgen/STAGE3-JOIN-PLAN.md R1–R8. R1 is the only join that may write name= — and you will not write it.

Constraints:
- Dummy TIN 000-000-000-00000, synthetic name, no real taxpayer data, no live COR.
- Local Save only into C:\eBIRForms\savefile\. Never Submit, Send, e-mail, Validate-for-submit, or Final Copy.
- Never write official_field_key or HTML name=. Never run map_tin.py --write. Never invent serialized_key. Never edit live rules/forms/. Never point rules/tools/build-*-package.ps1 at canonical snapshots. Never push refs/backup/*. Never merge.
- Serial: finish job n before n+1. W7 only after W1–W3. Do not skip to harvest/remint.
- Use Computer Use for the eBIRForms GUI (read the computer-use skill, initialize sky, attach to the existing BIRForms/eBIRForms window). Use python for hashes, XML name lists, leftover_keys, join_census, staging. rtk is missing; forms-corrected/ is absent and --tree is a recorded binding only.
- Live package is 7.9.6.1 (record vs manifest 7.9.6.0). 2200A tinA is L396/L1390/L2007, not L1943. Do not let 2000-dst-2018 steal 2000-v2018. Leftover pins until land: unique 8028, duplicate 4, claimed_unique 147, claimed_duplicate 4, claimed_absent 12, claimed 163.
- Staging only under tools/formgen/corrections/staging/<job>-<yyyymmdd>/ (copy full rules/forms first). Evidence JSON under tools/formgen/corrections/evidence/. Do not commit savefiles, PDFs, or .hta.

Autonomy: after each job, write the plan §8 JOB n packet, run that job’s checks, then immediately start the next. Ask me only if (a) a dialog looks like Submit, (b) a file looks like a real TIN, or (c) Computer Use requires a confirmation you cannot satisfy with dummy local Save. Otherwise assume dummy Save, staging, evidence, and continue.

W8 is authorized as evidence-only: commit evidence first, then staging if needed, open a PR to public hexuria/buwiz-forms as hexuria, do not merge. If git/gh cannot open the PR, finish W0–W7 and report that blocker instead of looping.

Verification / done when:
- leftover_keys.py --self-test still passes on the live tree until the W8 pin update.
- Each job has a §8 packet with savefile sha256, form/revision/package, HTA sha256, exe 7.9.6.1 vs 7.9.6.0, verbatim keys, staging path or none, pin status, NEXT.
- W1: staging serialized_key only where saveXML emitted the name; staging census claimed_absent 0 for 1601eq-2019 and 1701q-2018, or quoted spelling mismatch.
- W2: new staging snapshot; live 2000-v2018/2000ot-v2018 untouched; steal still forbidden.
- W3–W7: evidence only (R3/R7 classified not joined; harvest-rule proposals are JSON text, not code).
- W8: evidence-first commit + PR, not merged; no savefiles/PDFs/.hta in git.
- map_tin.py was never run with --write.

Keep going until that done-when holds or a hard stop above fires.
```

Companion to `/goal`. The slash-goal objective is the completion criterion;
this file is the long instruction the objective points at. Execute
[WINDOWS-EBIRFORMS-PLAN.md](WINDOWS-EBIRFORMS-PLAN.md) jobs **W0–W8** as the
sole operator. Policy remains
[HANDOFF-WINDOWS-EBIRFORMS.md](HANDOFF-WINDOWS-EBIRFORMS.md) and
[STAGE3-JOIN-PLAN.md](STAGE3-JOIN-PLAN.md).

This is **not** a remint, **not** a mapper, **not** print-parity.

---

## 0. Start of every continuation

1. Read this file, the plan, and the handoff if a claim is unclear.
2. List `tools/formgen/corrections/evidence/` and
   `tools/formgen/corrections/staging/` for packets already written.
3. Resume at the first incomplete job. Do not redo a job whose §8 packet
   and completion checks already hold.
4. If Offline eBIRForms is not running, launch `C:\eBIRForms\BIRForms.exe`
   and rediscover the extracted HTA GUID (it changes every package restart).
5. Re-hash the live HTA for the form you are about to Save. Quote both hashes
   if they diverge from the plan’s §1 table; keep going on observation.

---

## 1. Hard stops (ask Uriah; otherwise keep going)

Stop the **current job only** and paste dialog/path text when:

- a dialog looks like Submit / Send / e-mail / Final Copy / online filing;
- a file contains anything that looks like a real TIN (not `000-000-000-00000`);
- Computer Use requires a confirmation that cannot be satisfied with dummy
  local Save.

Do **not** stop for: Save preflight, missing `rtk`, missing
`forms-corrected/`, leftover_keys CLI exit 1 on a **staging** tree, HTA GUID
change, occ 2/3 disabled on 2200A, encrypted-vs-plaintext (try plaintext
Save first; decrypt dummy-only if needed).

---

## 2. Computer Use (GUI)

Read the computer-use skill completely, then `guidance.md` and
`confirmations.md`. Initialize once per JS session:

```js
if (!globalThis.sky) {
  const { sky } = await import("@oai/sky");
  globalThis.sky = sky;
}
```

Attach to the existing Offline eBIRForms window. Dummy profile only.
Fill TIN `000-000-000-00000` plus the minimum Save preflight. Click **Save**,
never Submit. Copy the new file out of `C:\eBIRForms\savefile\`
immediately (the client overwrites on navigation).

Preserve screenshots and exact dialog text in the evidence JSON, not in git
as binaries unless already the repo convention. Do not commit savefiles.

---

## 3. Python / inventory (no GUI)

```text
python tools/formgen/leftover_keys.py --self-test
python tools/formgen/leftover_keys.py --tree forms-corrected
```

`rtk` is missing. `forms-corrected/` is absent; `--tree` is a recorded
binding. The census does not read HTML.

Live leftover pins (must not silently “fix” on the live tree):

- leftover_unique 8028
- leftover_duplicate 4
- claimed_unique 147
- claimed_duplicate 4
- claimed_absent 12
- claimed 163

`leftover_keys.py` pins the **whole corpus** and does not write JSON on
FAIL. For W1/W2, copy `rules/forms` into staging, edit only the named
snapshots, then inspect those bundle rows via `join_census` /
`leftover_keys.build_census` (or read FAIL lines). Do not expect the pinned
CLI to exit 0 on staging until W8 updates `ACCEPTANCE` in the same commit
as the live inventory.

Never run `map_tin.py --write`. Never write `official_field_key` or HTML
`name=`. Never invent a `serialized_key`.

Hash with SHA-256. List XML names in document order with a small Python
script. Compare JSON with `jq -S -c`, not `diff`.

---

## 4. Staging and evidence layout

- Evidence: `tools/formgen/corrections/evidence/<job>-<yyyymmdd>.json`
  (one file per job is fine; W1 may be two forms in one JSON).
- Staging: `tools/formgen/corrections/staging/<job>-<yyyymmdd>/`
  Copy the **full** `rules/forms` tree first so later pin math still has
  43 inventories. Edit only the named snapshot.
- Live `rules/forms/` stays untouched until W8, and W8 still prefers
  evidence-first; live inventory edits ride with the pin update.
- Do not commit savefiles, PDFs, `.hta`, or decrypted XML.

---

## 5. Job loop (serial)

After each job: write the §8 packet into the evidence JSON **and** as the
turn’s last line block, run that job’s checks, then start the next job in
the **same** goal continuation. Do not wait for Uriah.

### W0 — pins (likely already done)

Replay leftover self-test. Record live exe 7.9.6.1
`a43a4599f95158e6ba0e7a1c4b88c4e2cf215ac86e53c24259cc69d1b664829c`
(58,411,008 bytes) vs manifest 7.9.6.0
`de8ef0815509d65189e6794e1f8135a5ecf5f2800005d1fc5c87043efd96dbca`.
Rediscover HTA GUID. Savefile dir may be empty — that is expected.
Packet, then W1.

### W1 — 1601EQ + 1701Q `serialized_key`

Open **1601EQ January 2018** (`BIR-Form1601EQ.hta`), dummy TIN, Save.
Repeat **1701Q January 2018** (`BIR-Form1701Qv2018.hta`, not
`BIR-Form1701Q.hta`). Spouse / page-2 TIN keys are out of scope.

Stage `serialized_key` only on rows the savefile emitted. Rows not emitted
stay without `serialized_key`.

Pass: staging census `claimed_absent` 0 for slugs `1601eq-2019` and
`1701q-2018`, **or** quoted spelling mismatch (catalog vs saveXML).

### W2 — 2000-DST inventory the catalog can see

Open **BIR Form 2000v2018** (Documentary Stamp),
`APPLICATIONNAME="2000v2018"`. Exclude legacy `BIR-Form2000.hta` and
2000-OT. Quote window title and printed header.

`rules/forms/2000-v2018` already has the four TIN keys. That does **not**
finish W2: catalog slug `2000-dst-2018` is `resolution: absent` by design.
Do not change `join_census.SLUG_RE` / `resolve_slug`. Do not steal
`2000-v2018`. Stage a **new** snapshot directory.

### W3 — 2200A occurrences

Live HTA: occ1 L396 `tinA` writable; occ2 L1390 `tinA_2` disabled; occ3
**L2007** `tinA_3` disabled. Handoff L1943 is stale. Same pattern for
`tinB` / `tinC` / `branchCode`. Type a distinct digit into occ 1 only,
Save, show which XML node changed. Do not enable disabled copies.
Table is the deliverable. No catalog / `harvest_tin` edit.

### W4 — R3 / R7 shape

Prefer 2550M February 2007. One money comb Save; then two repeating rows
if a band exists. Verdict exactly one of `R3-shaped`, `R7-shaped`,
`other` (quote the shape). Do not join.

### W5 — 2550M leftover leaves

Dummy Save with TIN, RDO, ZIP, address, email, taxpayer name if Save
allows. Partition 246 leftover_unique keys into `emitted` vs `silent`.
Harvest-rule proposal is JSON text, only if emitted + unique leaf in
bundle + one named printed box. Not a code change.

### W6 — leftover_duplicate

`frm1702q:txtTelNum` (2), `frm1707Av2021:txtI11Email` (2), `txtEmail` on
2551Q (2), `frm2200Av2020:registeredName` (3; HTA L439 / L1396 / L2013).
Caption/page/item per occurrence, or “one control serialized twice”.

### W7 — 1600WP agent TIN (only after W1–W3)

Catalog `extra/1600wp-2010/p1/tin-agent-*` is gapped. Item 5
`frm1600WP:txtTIN*` is C05, not this comb. Schedule `dtSched:txtTin1`…`10`
are payee TINs. Deliver HTA control id, or quote that Save emits no
distinct key.

### W8 — land (authorized by the `/goal` objective)

1. Evidence-only commit first.
2. If staging `fields.json` must become live, copy to a **new** snapshot
   dir (never overwrite a 7.9.6.0 snapshot in place), update leftover
   pins in the **same** commit after a live re-run.
3. Branch `gol/windows-ebirforms-obs`. Push `public` only. Open PR to
   `hexuria/buwiz-forms` as `hexuria`. **Do not merge.**
4. If `git`/`gh` cannot open the PR, finish W0–W7, write the packets,
   and report that blocker. Do not loop.

---

## 6. Packet template (every job)

```text
JOB n: <title>
  SAVEFILE: <path> sha256=<64 hex>
  FORM: <official code> <printed revision> package=<version>
  HTA: <path> sha256=<64 hex>
  EXE: 7.9.6.1 sha256=a43a4599… (vs manifest 7.9.6.0 de8ef081…)
  FINDING: <one paragraph, keys quoted verbatim>
  STAGING: <path or none>
  PINS: leftover_keys.py <unchanged | new counts>
  NEXT: <job number or stop>
```

---

## 7. Done-when (goal completion)

The `/goal` is complete only when all of these hold:

- W0–W7 packets exist under `tools/formgen/corrections/evidence/`.
- Live `leftover_keys.py --self-test` still passes until the W8 pin update.
- `map_tin.py --write` was never run (search the session / git diff).
- No live `official_field_key` / HTML `name=` writes.
- R3/R7 classified, not joined.
- W8: evidence-first commit + PR opened, **not** merged; or a written
  blocker that git/gh could not open the PR after W0–W7 finished.
- Git contains no savefiles, PDFs, or `.hta`.

Keep continuing until that is true or a hard stop in §1 fires.
