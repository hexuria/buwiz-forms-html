# 1701Q correctness audit

Status: **complete with one explicit safety-bounded unverified transition**.

Completed:

- Exact January 2018 form identity recorded.
- Installed 7.9.6.0 package, HTA, shared scripts, dummy save, official form PDF, and January 2018 guide hashed.
- Exact retained 7.9.5.0 package resources were decoded and compared against 7.9.6.0; the only 1701Q validation delta is spouse branch-code length 3 to maximum 5, already represented in the current rules.
- The virtualized 7.9.6 `chkt.exe` was directly captured, hash-pinned, and black-boxed with synthetic inputs; its 0/1/2/3 exit-code contract and the JavaScript-only `999999999` bypass are verified.
- Virtualized `Encrypt.exe` produced deterministic ciphertext from the dummy XML, and the exact repository crypto pipeline decrypted it byte-for-byte to the source; live Final Copy UI state remains separately unverified.
- All 172 editable-save keys preserved in order and matched to the existing locked inventory hash.
- All 172 field records have labels and printed item references; all 37 radio/checkbox records separate storage booleans from official HTA values/groups and bind their printed captions; Part IV payment subcolumns are explicit.
- Per-field spouse requiredness, schedule/profile/computed enablement, amended-only Item 59 behavior, and transition-specific exceptions are bound to exact HTA lines; no generic condition placeholders remain.
- Static Validate and Save first-error order extracted.
- Nineteen calculation nodes and dependency order extracted; every calculation is bound to its printed item.
- Item 45's sum of Items 41-44 and every printed 2018-2022/2023+ Item 46 bracket were cross-checked against the official PDF.
- The exact official 7.9.6 Item 45/46 JScript passed 66 headless cases: 64 threshold cases across both parties and two signed Item 45 sums.
- Save, Validate, Edit, Final Copy, and Submit source paths separated.
- Confirmed defects recorded without silently correcting official behavior.
- The live 7.9.6 UI confirmed the exact missing-year first-error dialog, acceptance of birth month `00`, acceptance of a blank spouse branch code, acceptance of blank spouse Item 25A, and the no-spouse Edit-state enablement defect.
- The live Submit / Final Copy path confirmed the existing-file/amended-return warning and reached the exact online-submission confirmation. That confirmation was canceled; no submission occurred and no 1701Q savefile or encrypted-copy timestamp changed.
- All six schema-bound JSON documents and all JSON fixtures pass the tracked validators.

Confirmed official defects/hazards:

1. Primary taxpayer TIN receives length checks but no checksum call in `validate()`.
2. `999999999` is explicitly accepted by the shared TIN wrapper as a testing bypass.
3. Spouse branch-code validation rejects only length greater than 5, allowing blank/short values.
4. The birth-date validator can admit month `00` and has a flawed `isNaN` expression.
5. The Item 52B cap error says `Item 52A`.
6. `validateYear()` says greater-than-or-equal while its predicate is strictly greater-than.
7. Item 40's guard is taxpayer-OSD OR spouse-OSD regardless of the party being computed.
8. Final Copy appears to reuse the narrow Save preflight rather than mandatory full Validate.
9. Validate rejects a blank Item 16A although the official guide says an unmarked deduction choice is deemed Itemized.
10. For spouse ATC II016, Item 52B remains enabled when spouse type Compensation Earner is selected, contrary to the guide's mixed-income rule.
11. enableSpouse targets taxpayer Item 14 instead of spouse Item 23, leaving Item 23 disabled on the Estate/Trust-to-individual transition.
12. Spouse Item 25A is not validated; blank falls through to OSD computation although the guide deems blank Itemized.
13. Edit restores spouse Schedule I/II inputs from taxpayer rate/method selections instead of spouse selections.
14. Edit re-enables spouse credits/payments and penalties even when no spouse exists, except for separately corrected Item 59B.
15. Shared Item 43/48 description controls are overwritten by either party schedule transition, breaking mixed taxpayer/spouse schedule states.

The only remaining uncertainty is the post-submission `txtFinalFlag`/reopen transition. It is explicit in `gaps.md` and cannot be observed without confirming an online tax submission, which is outside this research scope. Source inspection and the byte-exact offline encryption roundtrip remain the evidence for that unreachable transition.

Verification: `rtk powershell -NoProfile -ExecutionPolicy Bypass -File "\\Mac\goldcoders\reverse-engineer-ebir-forms\bir-print-parity\rules\validate.ps1" -RequireJsonSchema` passes with 22 JSON files, 172 fields, 40 validations, 19 calculations, 12 negative fixtures, structural audit pass, JSON Schema validation pass, and 6 schema-bound documents.
