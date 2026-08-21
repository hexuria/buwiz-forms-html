# 1601EQ correctness audit

Status: **complete with two explicit gaps**.

Completed:

- Bound the installed January 2018 HTA, help, official PDF, shared scripts, ATC catalog, and representative dummy save by hash.
- Proved that the local January 2019 PDF/guide are a materially different revision and kept them comparator-only.
- Inventoried 621 source-reachable serialized fields, including all dynamic ATC rows, with an ordered inventory hash.
- Extracted 48 ordered input, change, Validate, and Save-preflight rules with exact messages and source references.
- Extracted eight calculation nodes and dependency order.
- Generated the 211-record form-specific ATC fixture and the independent runtime-control inventory.
- Recorded Save, Validate, Edit, Final Copy, offline fallback, submit-success, and submit-retry source transitions.
- Confirmed two representative first-error dialogs in the official 7.9.6.0 UI without saving the 1601EQ return.
- Audited the only repository-side 1601EQ model: a generic registry entry with no form-specific validation/calculation implementation.

Confirmed official defects and hazards:

1. TIN validation checks only nonblank segments/branch; it accepts partial segments and performs no checksum validation.
2. Email validation checks only nonblank and the 20-character UI limit; it does not validate syntax.
3. The numeric key filter allows multiple decimal points, while malformed values are silently formatted as `0.00`.
4. Address lines are escaped and concatenated, then split at encoded character 127 on reopen, which can split a percent escape or change the original boundary.
5. Reopen overwrites the email stored in the return with the current global profile email.
6. Save uses a narrow three-field preflight rather than the ordered full Validate rules.
7. The package ATC catalog contains four 1601EQ records with an empty category that the Private/Government UI cannot reach.
8. Post-2018 package rates conflict with the official January 2019 form for WC651, WC710, WI661, and WC661.
9. Submit / Final Copy exposes no separate ordinary offline-final action; encrypted offline output is reached only after confirmation and a failed connection probe.
10. Payment control IDs are offset by two from their January 2018 printed items (`txtAgency33..36` print as Items 31..34), creating a binding hazard.

Repository mismatch:

- `crates/bir-core/src/forms/registry.rs` marks 1601EQ as `requires_employees: true`. Official help defines the filer as every withholding agent/payor required to deduct and withhold expanded taxes, including corporations, government disbursing officers, fiduciaries, and representatives. Employee status is not an official eligibility condition. No renderer or runtime behavior was changed.

The two remaining gaps are enumerated in `gaps.md`; neither is hidden as a pass.

Verification: the generalized `rules/validate.ps1 -RequireJsonSchema` audit passes for both completed forms with 793 fields, 88 validations, 27 calculations, 34 negative fixtures, and 11 schema-bound documents.
