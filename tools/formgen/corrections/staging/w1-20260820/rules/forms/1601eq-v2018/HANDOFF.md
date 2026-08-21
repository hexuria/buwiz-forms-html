# 1601EQ validation-research handoff

Updated: 2026-07-23

## Objective

Extract and audit official Offline eBIRForms validation behavior for all forms in `C:\Mac\Home\Downloads\forms\FORM_BUILD_PRIORITY.md`, strictly in priority order.

Completed forms: 1701Q January 2018 and 1601EQ January 2018. The next priority form is 1702Q.

Do not change renderers, migration/release evidence, or capability flags. Do not submit, use real taxpayer data, commit, or push.

## Checkout

Authoritative checkout: `\\Mac\goldcoders\reverse-engineer-ebir-forms\bir-print-parity` on branch `codex/print-preview-parity`. Do not use the sibling `bir` checkout. Prefix repository commands with `rtk`.

## 1601EQ integrity state

- form identity: January 2018, runtime ID `1601eq-v2018`
- package: 7.9.6.0
- fields: 621
- runtime serializable elements: 621
- validations: 48
- calculations: 8
- ATC records: 211
- negative fixtures: 22
- live dialogs: 2 exact first-error observations
- explicit gaps: 2
- all-form structural audit: pass (2 forms, 793 fields, 88 validations, 27 calculations, 34 negative fixtures)
- JSON Schema validation: pass (11 schema-bound documents)

The local `1601EQv2019` PDF/guide are comparator-only. Do not merge their Item 24 or shifted numbering into the January 2018 rules.

Run validation with:

`rtk powershell -NoProfile -ExecutionPolicy Bypass -File "\\Mac\goldcoders\reverse-engineer-ebir-forms\bir-print-parity\rules\validate.ps1" -RequireJsonSchema`

## Safety disclosure

The 1601EQ blank form was not saved or finalized. While navigating from the previously open synthetic 1701Q form, a stale menu index created `C:\eBIRForms\savefile\00000000000000-1701Qv2018-2026Q1V2.xml`, and Main Screen/Fill-up navigation rewrote `C:\eBIRForms\profile\00000000000000.xml`. Both were left untouched after discovery and are pinned in the 1601EQ live-observation fixture.

## Next form

Begin 1702Q only after the 1601EQ package and global index validate. First resolve its exact runtime revision/package identity before extracting any rule.
