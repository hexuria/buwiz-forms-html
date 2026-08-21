# 1701Q validation-research handoff

Updated: 2026-07-23

## Objective

Continue the persistent objective to extract, organize, verify, and audit the official Offline eBIRForms validation rules for all 43 forms in C:\Mac\Home\Downloads\forms\FORM_BUILD_PRIORITY.md, strictly in priority order.

Completed form: 1701Q January 2018. The next priority form is 1601EQ.

Do not modify renderers, migration/release evidence, or capability flags. Do not submit, use real taxpayer data, commit, or push. Preserve all uncommitted work.

## Checkout

Authoritative checkout:
\\Mac\goldcoders\reverse-engineer-ebir-forms\bir-print-parity

Intended branch: codex/print-preview-parity. Do not use the sibling bir checkout. Prefix repository commands with rtk.

Computer control initialized successfully on 2026-07-23 and attached to the official 1701Q HTA. The live observations are recorded in `fixtures/live-ui-observations-v1.json`.

## Current integrity state

Last validation result:

- JSON files: 22
- fields: 172
- validations: 40
- calculations: 19
- negative fixtures: 12
- structural audit: pass
- JSON schema validation: pass
- schema documents: 6

Validation command:

rtk powershell -NoProfile -ExecutionPolicy Bypass -File "\\Mac\goldcoders\reverse-engineer-ebir-forms\bir-print-parity\rules\validate.ps1" -RequireJsonSchema

Manifest status is complete with one explicit safety-bounded unverified transition. No later form has been started. The 2026-07-23 tracked validation passed with 22 JSON files, 172 fields, 40 validations, 19 calculations, 12 negative fixtures, and 6 schema-bound documents.

## Explicit remaining gap

The post-submission `txtFinalFlag`/reopen transition is deliberately unobserved. The live Submit / Final Copy path reached the explicit online-submission confirmation, which was canceled. Confirming it would attempt an online tax submission and is outside scope. Direct encryption and repository-compatible byte-exact decryption are verified.

## Computer-use resume procedure

Read completely:
C:\Users\uriah\.codex\plugins\cache\openai-bundled\computer-use\26.715.72028\skills\computer-use\SKILL.md

Initialize through:
C:/Users/uriah/.codex/plugins/cache/openai-bundled/computer-use/26.715.72028/scripts/computer-use-client.mjs

Required initialization:

if (!globalThis.sky) {
  const { setupComputerUseRuntime } = await import("C:/Users/uriah/.codex/plugins/cache/openai-bundled/computer-use/26.715.72028/scripts/computer-use-client.mjs");
  await setupComputerUseRuntime({ globals: globalThis });
}

Then read sky.documentation("guidance") and sky.documentation("confirmations"), plus API documentation when needed. Attach to the user's existing eBIRForms 7.9.6 window. Use only dummy data. Never click Submit or allow network transmission. Preserve screenshots and exact dialog text. Keep UI evidence distinct from source, runtime-JScript, and runtime-binary evidence.

## Authoritative hashes

Installed live authority:
- C:\eBIRForms\BIRForms.exe
- 7.9.6.0, 57,506,304 bytes
- SHA-256 de8ef0815509d65189e6794e1f8135a5ecf5f2800005d1fc5c87043efd96dbca
- decoded 1701Q resource 170: 5f164dde6154b96f28e23656ed2ef29406010ee3f94333e88ea6eb107fe589a0
- eBIRTools resource 557: 7d0ceb5aad2c0eb90aeca189d6104ff05163ecd1820379f456125634ff7460f7
- string-util resource 570: bc7f86f70bf993389a3a0135dcbd76c3e370c49d2eb95e2fc66ff318a2ebe43c
- string-util2014 resource 571: ca42592694e7416a15eca97fa25491c01da17e383038fc97dd9d6261e67bcf7d

Historical comparator only:
- \\Mac\goldcoders\reverse-engineer-ebir-forms\BIRForms.exe
- 7.9.5.0, 56,572,928 bytes
- SHA-256 3d087545564531de1fbe8fb28f086ce6398e18608c54a0ea33353042665917eb
- decoded 1701Q resource 170: 42f25e268aefe881a2e1fa1d73ac4c47ef17d3ad236b3cbfb7b62af22949592d
- eBIRTools resource 553: aaf5dbe9593ca81f808540e537353f297f9bd8638e488ea5161673e3985a91bc

Official PDF:
C:\Mac\Home\Downloads\forms\1701Qv2018\1701Q Jan 2018 final rev2_copy.pdf
SHA-256 c731d3f12556e6f19ab81f6113ca7c4a23f7ed099675c03451ac0074d96b85ed

Official guide:
C:\Mac\Home\Downloads\forms\1701Qv2018\1701Q Guide Jan 2018.pdf
SHA-256 ff07962229015a50b0aa169f91fa32e10c534f6730de5bf59263e22d34e270bc

Dummy XML:
C:\eBIRForms\savefile\00000000000000-1701Qv2018-2026Q1V1.xml
11,898 bytes
SHA-256 69beaa41b045b44e1ccca742d56a06a3ec505375b80851d6592c60520cc9303e
172 unique fields
ordered field-ID SHA-256 a135fd015a3e3c349f4e6baffce52317734cb478e8a3a1d62072901c050acb3d

## 7.9.5 to 7.9.6 comparison

Both decoded HTAs have 5,234 nonblank lines after normalization. Only three line pairs differ:

1. SYSMENU="NO" became case-equivalent SYSMENU="yes".
2. Spouse branch maxlength changed from 3 to 5.
3. Validation changed from exactly 3 to rejecting only lengths greater than 5.

No other validation, calculation, or state-transition source changed. Treat 7.9.6 as current authority and 7.9.5 only as historical comparison.

## Package and virtual helpers

rules/tools/audit-ebir-package-resources.ps1 now enumerates PE resource types 1, 2, 3, 5, 6, 12, 14, 16, 23, and 24; reports read failures; scans raw and XOR-decoded resources for MZ; and maps type-23 resources to manifest paths.

Both packages completed with zero read errors and no raw/XOR-decoded MZ resource. Their manifests name no helper executable.

The virtual filesystem nevertheless resolves direct opens under C:\eBIRForms while directory enumeration and Get-Item cannot see:

- chkt.exe: 38,400 bytes; SHA-256 c00bd4131a725af53f48c6385d3332c4b789e15441bf52bbac73117c96c1b0ac
- Encrypt.exe: 489,452 bytes; SHA-256 429337f44f84b93cd1095df48c8f3265e5ede7c646d1b48d9b80f4f92de74d2c
- ebfSFTP.exe: 9,216 bytes; SHA-256 c6ba25014d30a11b97d9d90c3b87f2f0c13d35ef6188ea5c086f48b3933d297f
- cFTPSend.exe: 335,360 bytes; SHA-256 5d3dbda56e3ffffefb23f2fd46a5af0c0decc389d70921c453c3f813bb806262

## Checksum evidence

chkt.exe is unsigned native Free Pascal 2.6.4 i386-Win32. Synthetic black-box behavior:

- 0: accepted checksum
- 1: checksum mismatch
- 2: non-nine-digit/nonnumeric argument
- 3: missing/empty argument

For prefix 12345678 only suffix 8 succeeds. chkt.exe rejects 999999999 with 1; getTinChkCode overrides it to success, proving a JavaScript-only bypass.

See fixtures/checksum-cases.json. Rules 1701q-validate-015 and 1701q-blur-003 bind runtime-binary evidence. The validation schema includes the additive runtime-binary category.

## Calculation evidence

Exact 7.9.6 computetxt45, computetxt46, formatCurrency, and NumWithComma ran under Windows Script Host JScript with a minimal fake DOM. All 66 cases passed: 64 Item 46 threshold cases for both parties across both tax tables, plus two signed Item 45 sums.

See fixtures/calculation-boundaries.json.

## Encryption evidence

Virtual Encrypt.exe ran only on temporary dummy XML copies:

- input: 11,898 bytes, SHA-256 69beaa41...
- output: 1,463 bytes, SHA-256 4e7f279f...
- exit 0
- repeated encryption produced identical ciphertext

The exact crates/bir-core/src/crypto.rs pipeline was reproduced on Windows: SHA-256 key derivation, AES-256 ECB-derived IV, DCPcrypt CBC plus partial tail, zlib. It recovered 11,898 bytes with SHA-256 69beaa41..., byte-for-byte identical to the source.

See fixtures/encryption-roundtrip.json. All temporary files were deleted. This proves encryption/decrypt compatibility and preservation, not the live Final Copy flag/reopen transition.

## Confirmed official defects

1. Taxpayer TIN gets length checks but no checksum call.
2. JavaScript accepts 999999999 although chkt.exe rejects it.
3. Spouse branch rejects only length greater than 5, accepting blank/short.
4. Birth date can admit month 00 and has flawed isNaN logic.
5. Item 52B cap error says Item 52A.
6. validateYear message says >= while predicate is >.
7. Item 40 guard is taxpayer-OSD OR spouse-OSD regardless of party.
8. Final Copy appears to reuse narrow Save preflight, not full Validate.
9. Blank Item 16A is rejected although guide defaults blank to Itemized.
10. Spouse II016 Item 52B remains enabled for Compensation Earner.
11. enableSpouse enables taxpayer Item 14 instead of spouse Item 23.
12. Blank spouse Item 25A falls through to OSD although guide defaults Itemized.
13. Edit restores spouse schedules from taxpayer selections.
14. Edit enables spouse credits/penalties without a spouse, except Item 59B.
15. Shared Item 43/48 descriptions are overwritten by either party transition.

## Key files

- manifest.json: assets, counts, artifact index
- fields.json: 172 typed fields
- validations.json: 40 ordered rules
- calculations.json: 19 calculations
- workflow.json: Save/Validate/Edit/Final Copy/Submit paths
- evidence.md, audit.md, gaps.md
- fixtures/negative-cases.json: 11 dialog fixtures
- fixtures/positive-minimal.json
- fixtures/calculation-boundaries.json
- fixtures/checksum-cases.json
- fixtures/encryption-roundtrip.json
- fixtures/live-ui-observations-v1.json
- ../../schema/validations.schema.json
- ../../tools/extract-1701q-fields.ps1
- ../../tools/audit-ebir-package-resources.ps1
- HANDOFF.md

## Completion rule

The requirement-by-requirement completion audit is recorded in `audit.md`. Continue with 1601EQ without changing renderer, migration, release, or capability metadata.
