# Handoff — after live 1701Q Save / View / Submit-cancel

Dated 2026-08-22. Frozen-next (0619E sit, 1701Q spouse harvest, vendor) and
the live 1701Q Save evidence are **done and merged**. Start a **new** session
from **merged `main`**, in a **git worktree**. Do not resume the ARM Windows
chat.

**Windows is not required for the next product task** (draft decode). It is
required only if you need another official `BIRForms.exe` sit.

---

## Paste this as the first message

```text
Read tools/formgen/HANDOFF-AFTER-1701Q.md in hexuria/buwiz-forms-html (this file).

You are a new session. Do not continue the old ARM Windows chat. Work only in git worktrees branched from origin/main. Do not edit the dirty checkouts C:\Users\uriah\Code\buwiz-forms or C:\Users\uriah\Code\buwiz-forms-html if they still exist.

Goal: official-compatible 1701Q in Buwiz — emit/import the same 172-field saveXML(false) envelope the live package writes. Not leftover uniqueness harvest. Not a remint. Not flipping QUEUE_SUBMISSION_SUPPORTED. Not transport.rs. Not confirming online filing.

Repos (PRs merged; no open product/html PRs as of 2026-08-22 except this handoff if still open):
- Product hexuria/buwiz-forms main cac025ff = #35 vendor 1701Q spouse TIN freeze.
- HTML hexuria/buwiz-forms-html main cf39521 = #3 live Save + round-trip + View + decode sit + Submit-cancel evidence.

This machine does not need to be Windows. Prefer a fast x64 (Linux/macOS/Windows) for cargo. Official BIRForms.exe sits still need Windows + the hash-locked 7.9.6.1 package; skip those unless Uriah asks.

Do this now:
1. Product worktree from origin/main. Make Form1701QDraft::from_bir_xml_payload succeed or fail closed on a TEMP copy of the live dummy Save (do not commit the XML). Today a minimum official Save is predicted to Err on filer_type, atc, and tax_rate (all radios false). to_bir_xml_payload is not reached. QUEUE_SUBMISSION_SUPPORTED stays false.
2. Land that as a product PR with tests that do not require BIRForms.exe. Run cargo test -p bir-core on this machine or CI x64 (Windows ARM failed: aws-lc-sys LNK1181, openssl vendored needs perl).
3. Do not click OK on the official Submit confirm. Do not edit crates/bir-core/src/transport.rs. Do not invent frm… keys. Do not copy leftover uniqueness onto name= / official_field_key. R3/R7 stay closed.

Hard stops: dummy TIN 000-000-000-00000 only; synthetic name DELA CRUZ JUAN; do not use real TIN 261-708-015-00000; do not commit saveXML, .hta, or real TINs; do not force-push main; do not commit Co-authored-by: Cursor.

Ask Uriah only if you want to change decoder policy (reject incomplete Saves vs accept them as drafts) or start another live official-client sit.
```

---

## 0. Windows vs a faster computer

Need **this Windows box / any Windows with eBIRForms** only for:

- Launching `C:\eBIRForms\BIRForms.exe` (must start with cwd `C:\eBIRForms`)
- Dummy-TIN patch on the extracted `%TEMP%\{GUID}\js\string-util.js`
- Live Save / View / Submit GUI

**Do not need Windows** (and this ARM box is a poor cargo host) for:

- `Form1701QDraft::from_bir_xml_payload` / `to_bir_xml_payload`
- Product tests and PRs
- Reading evidence JSON already on html `main`

If the faster computer is macOS or Linux: do the decoder. Skip official GUI.
If it is Windows x64: even better for `bir-core`; reinstall eBIRForms only when
Uriah wants another live sit, then verify the exe hash below.

`rtk` is missing on the ARM box. Use `python` / `cargo` / `gh`. PowerShell: no
bash HEREDOC, no `\` continuations in CI YAML.

---

## 1. New machine setup

```text
git clone git@github.com:hexuria/buwiz-forms.git
git clone git@github.com:hexuria/buwiz-forms-html.git

cd buwiz-forms
git fetch origin main
git worktree add -b gol/1701q-draft-decode ../buwiz-forms-wt-1701q-decode origin/main

cd ../buwiz-forms-html
git fetch origin main
# evidence is already on main; no html worktree required unless you add packets
```

Open Cursor on the **product** worktree. If `move_agent_to_root` aborts, File →
Open Folder on that worktree and start the chat there. Do not start from a
dirty main checkout.

Push the new branch **before** `move_agent_to_root` (that tool fetches
`origin/<local-branch>`).

---

## 2. What already landed (do not redo)

| Increment | Result | Where |
| --- | --- | --- |
| 0619E freeze sit | Dummy TIN stamps `frm0619E:txtTIN1/2/3` + `txtBranchCode`. Local `bir-print` may fail on Windows ARM `clang-cl`; CI x64 is the test. | product `main` via earlier freeze PRs |
| 1701Q spouse TIN harvest | Printed PART II spouse TIN → `frm1701q:txtSpouseTIN1/2/3`, `txtSpouseBranchCode` | html [#2](https://github.com/hexuria/buwiz-forms-html/pull/2), product [#35](https://github.com/hexuria/buwiz-forms/pull/35) |
| Live dummy Save | Official Fill-up + Save only. File `00000000000000-1701Qv2018-2026Q1.xml`, 11877 bytes, sha256 `bf11bbde0f0f01a416d90bffad00c2eda636604259c49f66e33388e26a259ccc` | html [#3](https://github.com/hexuria/buwiz-forms-html/pull/3) `live-save-1701q-20260821.json` |
| Writer contract | Live Save is byte-identical to `OFFICIAL_EDITABLE_FIELD_IDS` (172 keys, CRLF envelope). Compare with `read_bytes()`, not Python `Path.read_text()` (strips `\r`). | `roundtrip-1701q-20260821.json` |
| Official View | Header TIN `000-000-000-00000`, year 2026, First quarter, RDO 018, DELA CRUZ JUAN. Savefile unchanged by View. | `open-view-1701q-20260821.json` |
| Draft decode sit | `from_bir_xml_payload` **not executed** on ARM (aws-lc-sys / openssl). Source-trace: Err on `filer_type`, `atc`, `tax_rate`. Amounts are `0.00` so `parse_money` would pass. `deduction_method` is optional. | `draft-decode-1701q-20260822.json` |
| Submit / Final Copy | Clicked Submit. Confirm quoted below. **Cancel** (not OK). `QUEUE_SUBMISSION_SUPPORTED` still false. `transport.rs` untouched. | `submit-1701q-20260822.json` |

Evidence directory:

`tools/formgen/corrections/evidence/` on html `main`.

---

## 3. Official package (Windows sits only)

```text
C:\eBIRForms\BIRForms.exe
product 7.9.6.1
58411008 bytes
sha256 a43a4599f95158e6ba0e7a1c4b88c4e2cf215ac86e53c24259cc69d1b664829c
packed exe untouched
launch: Start-Process -FilePath C:\eBIRForms\BIRForms.exe -WorkingDirectory C:\eBIRForms
```

Dummy TIN patch is on the **extracted** `js\string-util.js` only (GUID folder
under `%TEMP%` changes on package restart). `getTinChkCode` short-circuits
`000000000` / `999999999` / `222222222` to `return 0` before `chkt.exe`.

Dummy profile: `C:\eBIRForms\profile\00000000000000.xml` — zeros, DELA CRUZ
JUAN, RDO 018, RETAIL, Olongapo/Zambales, zip 2200. Do not put the profile
email in git JSON if avoidable.

**Do not use** `C:\eBIRForms\profile\26170801500000.xml` (real TIN). Do not
copy that file to another computer.

Live savefile after the Submit sit was restored to the Save packet bytes
(`bf11bbde…`). Keep it that way unless a new sit intentionally replaces it.
XML stays out of git (html `.gitignore` has `*.xml`).

---

## 4. Submit sit (already done; do not confirm)

Exact dialog (win32 `#32770`, buttons **OK** / **Cancel**):

```text
Please ensure that you have INTERNET access and a VALID email address is indicated in your tax return.

Are you sure you want to submit?
```

Clicked **Cancel**. OK would continue toward filing (`checkNetConnection`, then
`ebirEnroll` or `txtFinalFlag=3` + `saveEncryptedProfile`). That is still out
of scope.

`openAlertEmail()` calls `conService.initConConfig` **before** the confirm
(sync XHR to `tinDispatcherSFTP.php` with dummy TIN `000000000` and
`1701Qv2018`). 1701Q is not on the eFPS allowlist. No packet capture.

A rewrite of the savefile was observed at Submit-click time (six fields:
`optType_1`, `optATC_5`, `optTaxRate_2`, DOB `08/17/1988`) with `txtFinalFlag`
still `0`. Restored from `%TEMP%\buwiz-live-1701q-20260821.xml`. Do not treat
that rewrite as a final copy.

Post-submission `txtFinalFlag` / reopen after a **confirmed** OK is still
deliberately unobserved.

---

## 5. Decoder gap (next product work)

Source: `crates/bir-core/src/forms/form_1701q_xml.rs`
`Form1701QDraft::from_bir_xml_payload` → `from_bir_field_map`.

Live minimum Save has:

- envelope + 172 keys + `txtFinalFlag=0` + `txtEnroll=N` + empty secret
- quarter 1, not amended, dummy TIN, name/address/LOB JS-escaped
- all `optType_*`, `optATC_*`, `optTaxRate_*` false
- paired amounts and `txt31` = `0.00`

`parse_one_of(..., required: true)` therefore errors on `filer_type`, `atc`,
`tax_rate` and returns **before** constructing the draft, so
`to_bir_xml_payload` never runs.

Copy XML to `%TEMP%` (or CI artifact not committed). Env-gated or fixture tests
must not check in the live savefile.

`QUEUE_SUBMISSION_SUPPORTED` is false on every form, including 1701Q. Leave it.

---

## 6. Hard stops (unchanged)

- Do not invent `frm…` keys.
- Do not copy leftover uniqueness (`txtAddress`, `txtRDOCode`, …) onto `name=`
  or `official_field_key`.
- Do not continue `gol/harvest-2550m-headers`. R3/R7 stay closed.
- Do not edit `crates/bir-core/src/transport.rs` unless a Submit sit proves
  the product client must change — and Uriah has started that sit.
- Do not commit saveXML, `.hta`, real TINs, `tools/_tmp_*`.
- Do not force-push `main`.
- Do not commit `Co-authored-by: Cursor`.

---

## 7. Worktrees on the ARM Windows box

These were session scratch trees. After this handoff PR, they are safe to
remove if clean and unused. **Do not delete** the dirty main checkouts
(`buwiz-forms`, `buwiz-forms-html`); they are reserved even when behind
`origin/main`.

Do not copy leftover `target/` build dirs to the new computer; let cargo
rebuild there.
