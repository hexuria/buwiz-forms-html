# Evidence

## Official assets inspected

- Offline eBIRForms 7.9.6.0 `BIRForms.exe` (installed live runtime): SHA-256 `de8ef0815509d65189e6794e1f8135a5ecf5f2800005d1fc5c87043efd96dbca`.
- Offline eBIRForms 7.9.5.0 `BIRForms.exe` (retained historical comparator): SHA-256 `3d087545564531de1fbe8fb28f086ce6398e18608c54a0ea33353042665917eb`.
- Runtime-extracted `BIR-Form1701Qv2018.hta`: SHA-256 `5f164dde6154b96f28e23656ed2ef29406010ee3f94333e88ea6eb107fe589a0`.
- `string-util.js`: SHA-256 `bc7f86f70bf993389a3a0135dcbd76c3e370c49d2eb95e2fc66ff318a2ebe43c`.
- `string-util2014.js`: SHA-256 `ca42592694e7416a15eca97fa25491c01da17e383038fc97dd9d6261e67bcf7d`.
- `eBIRTools.vbs`: SHA-256 `7d0ceb5aad2c0eb90aeca189d6104ff05163ecd1820379f456125634ff7460f7`.
- Virtualized `chkt.exe`: 38,400-byte native PE32, SHA-256 `c00bd4131a725af53f48c6385d3332c4b789e15441bf52bbac73117c96c1b0ac`.
- Virtualized `Encrypt.exe`: 489,452-byte native PE32, SHA-256 `429337f44f84b93cd1095df48c8f3265e5ede7c646d1b48d9b80f4f92de74d2c`.
- Dummy editable save `...1701Qv2018-2026Q1V1.xml`: SHA-256 `69beaa41b045b44e1ccca742d56a06a3ec505375b80851d6592c60520cc9303e`.
- January 2018 official PDF: SHA-256 `c731d3f12556e6f19ab81f6113ca7c4a23f7ed099675c03451ac0074d96b85ed`.

## Inventory proof

The dummy editable save has 172 unique serialized keys. Joining those keys in emitted order with a trailing newline hashes to `a135fd015a3e3c349f4e6baffce52317734cb478e8a3a1d62072901c050acb3d`, exactly matching `EXACT_EDITABLE_FIELD_IDS_SHA256` in the existing Rust mapping. The runtime has 173 serializable elements because `txtAddress` and `txtAddress2` are combined into one editable-save field.

The generated field inventory has no null labels. All 37 radio/checkbox records preserve the official HTA choice value, group, and printed caption separately from the XML serialized true/false selection state, and Part IV payment fields identify their printed subcolumns. No radio-button HTML value is treated as a saved default.

## Calculation cross-check

All 19 calculation records cite their printed form item. The official PDF states Item 45 is the sum of Items 41 through 44. Its 2018-2022 and 2023+ Item 46 tables match the thresholds, bases, and rates embedded in the inspected HTA.

The exact hash-pinned 7.9.6 `computetxt45`, `computetxt46`, `formatCurrency`, and `NumWithComma` function bodies were executed under Windows Script Host JScript with a minimal fake DOM. All 66 explicit cases passed: 64 Item 46 executions (32 threshold-adjacent inputs for both taxpayer and spouse) and two signed Item 45 sums. Inputs, expected formatted outputs, source hashes, source ranges, engine, and the observed pass are preserved in `fixtures/calculation-boundaries.json`; the temporary JScript file was deleted.

## Package distinction

The retained 7.9.5.0 package at `\\Mac\goldcoders\reverse-engineer-ebir-forms\BIRForms.exe` is the exact package pinned by Rust: 56,572,928 bytes, package SHA-256 `3d087545564531de1fbe8fb28f086ce6398e18608c54a0ea33353042665917eb`, resource-manifest SHA-256 `c8811837405fd76d8924a1c04a6f283a9ed448e3792753da21aaf6ceea191249`, decoded 1701Q resource 170 SHA-256 `42f25e268aefe881a2e1fa1d73ac4c47ef17d3ad236b3cbfb7b62af22949592d`, and decoded eBIRTools resource 553 SHA-256 `aaf5dbe9593ca81f808540e537353f297f9bd8638e488ea5161673e3985a91bc`.

The installed 7.9.6.0 runtime remains the current authority: 57,506,304 bytes, package SHA-256 `de8ef0815509d65189e6794e1f8135a5ecf5f2800005d1fc5c87043efd96dbca`, resource-manifest SHA-256 `f667d656a739e2aef1182d0f7c5a2182de96fa8479aaf33912d9b981947ab2c7`, decoded 1701Q resource 170 SHA-256 `5f164dde6154b96f28e23656ed2ef29406010ee3f94333e88ea6eb107fe589a0`, and decoded eBIRTools resource 557 SHA-256 `7d0ceb5aad2c0eb90aeca189d6104ff05163ecd1820379f456125634ff7460f7`.

After blank-line normalization, the 1701Q HTAs have the same 5,234 nonblank lines and only three changed line pairs: `SYSMENU="NO"` became case-equivalent `"yes"`; spouse branch-code `maxlength` changed from 3 to 5; and validation changed from requiring length exactly 3 to rejecting only lengths greater than 5. No calculation, state-transition, or other validation source changed. The tracked 7.9.6 rules already encode the current blank/short spouse branch-code acceptance.

## Official guide

- BIR URL: https://bir-cdn.bir.gov.ph/local/pdf/1701Q%20Guide%20Jan%202018.pdf
- Retrieved: 2026-07-22
- Local file: C:\Mac\Home\Downloads\forms\1701Qv2018\1701Q Guide Jan 2018.pdf
- SHA-256: `ff07962229015a50b0aa169f91fa32e10c534f6730de5bf59263e22d34e270bc`
- Size: 152,170 bytes

The guide binds Q1/Q2/Q3 deadlines, the five conditional attachments, the deduction-method default, and the mixed-income exclusion from the P250,000 reduction. These findings are represented in `workflow.json`, `validations.json`, and `fields.json`.
## Package and virtual-helper provenance

- The expanded package auditor enumerates all numeric resources across PE types 1, 2, 3, 5, 6, 12, 14, 16, 23, and 24. Both 7.9.5 and 7.9.6 completed with zero read errors and contain no raw or XOR-decoded `MZ` resource. Their type-23 manifests likewise name no helper executable.
- The package virtualization layer nevertheless resolves direct opens of `C:\eBIRForms\chkt.exe`, `Encrypt.exe`, `ebfSFTP.exe`, and `cFTPSend.exe` while directory enumeration and `Get-Item` cannot see them. Their respective SHA-256 hashes are `c00bd413...`, `429337f4...`, `c6ba2501...`, and `5d3dbda5...`.
- `chkt.exe` is a 38,400-byte unsigned native Free Pascal 2.6.4 i386-Win32 program. Synthetic black-box cases establish its contract: exit/stdout 0 for accepted checksum, 1 for checksum mismatch, 2 for non-nine-digit or nonnumeric input, and 3 for a missing argument. For prefix `12345678`, only suffix `8` succeeds.
- The helper rejects `999999999` with exit 1; `getTinChkCode` then explicitly overrides that result to success. This proves the testing bypass belongs to JavaScript rather than the checksum executable. Full synthetic observations are in `fixtures/checksum-cases.json`.
- The normalized `ValidateTinWChkDgt` body is identical across 7.9.5 and 7.9.6. The current 7.9.6 transport entry point calls virtualized `ebfSFTP.exe`; the former `cFTPSend.exe` path remains as `RenameAndSendFile_original`.

## Offline encryption roundtrip

The virtualized 7.9.6 `Encrypt.exe` was invoked only on a temporary byte-for-byte copy of the dummy editable XML. It exited 0 and transformed 11,898 plaintext bytes (SHA-256 `69beaa41...`) into 1,463 ciphertext bytes (SHA-256 `4e7f279f...`). A second independent encryption produced the same ciphertext hash, proving deterministic output.

The exact pipeline implemented in `crates/bir-core/src/crypto.rs`SHA-256 passphrase key derivation, AES-256 ECB-derived IV, DCPcrypt CBC with its partial-block tail, and zlib decompressionrecovered 11,898 bytes with SHA-256 `69beaa41...`. The result was byte-for-byte identical to the source XML. `fixtures/encryption-roundtrip.json` preserves the full hashes and observations. This proves helper/repository compatibility and data preservation, but not the live Final Copy UI's `txtFinalFlag` transition or reopen behavior; that narrower gap remains open.

## Dynamic-condition audit

All 172 generated fields now have explicit requiredness and enablement metadata where the official HTA has dynamic behavior; no generic required/enabled placeholders remain. The audit binds spouse-TIN activation, spouse type/ATC/rate dependencies, Schedule I/II branches, computed locks, profile-owned controls, amended-only Item 59, and Edit-state behavior.

Five additional source-confirmed defects were found:

1. enableSpouse enables taxpayer Item 14 instead of spouse Item 23 on a taxpayer-type transition.
2. Blank spouse Item 25A passes Validate and Item 41B computes it as OSD, contrary to the guide Itemized default.
3. Edit restores spouse schedules from taxpayer rate/method selections.
4. Edit enables spouse credits/payments and penalties when no spouse exists, except Item 59B.
5. Shared Item 43/48 description enablement is overwritten by either party transition in mixed-schedule cases.
## Live 7.9.6 UI observations

Computer control initialized successfully on 2026-07-23 and attached to the already-running official `BIR Form No. 1701Qv2018` HTA. Only the dummy profile was used.

- Clearing Item 1 produced the exact first-error dialog `Please enter a valid year in Item 1.` The year was restored to 2026 and Save was not clicked.
- Item 11 value `00/01/1990` passed the ordered Validate sequence and produced the exact validation-success dialog.
- After a successful no-spouse validation, Edit enabled Items 55B-58B, 60B-61B, and 64B-66B while leaving 59B disabled, confirming the source-derived state defect.
- Dummy spouse TIN `123-456-788`, blank branch code, RDO 018, Professional, ATC II014, a nonempty dummy spouse name, and blank Item 25A passed Validate and produced the exact success dialog.
- Submit / Final Copy with a non-amended existing return showed the exact amended-return warning. With Amended Return set to Yes, the path reached the exact online-submission confirmation. Cancel was selected; no online submission was confirmed and no 1701Q savefile or IAF_RDO_Copy timestamp changed.

The reproducible observations and exact dialogs are in `fixtures/live-ui-observations-v1.json`. The post-submission `txtFinalFlag`/reopen transition remains deliberately unobserved because reaching it requires confirming an online tax submission.
