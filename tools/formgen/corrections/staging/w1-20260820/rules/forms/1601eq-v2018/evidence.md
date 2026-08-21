# Evidence

## Revision identity

- Installed package: Offline eBIRForms 7.9.6.0.
- Runtime HTA: `BIR-Form1601EQ.hta`, 268,182 bytes, SHA-256 `cd56bf18d1da2127d578af611fe8005fe49913a65781bb603b22b31bbe548b96`.
- Runtime help: `Help1601EQ.hta`, 17,141 bytes, SHA-256 `9f531a985a89e980c8870fc5fa9d6341799dd1fb7fc900284f28668ac62edf05`.
- Official January 2018 PDF: 902,380 bytes, SHA-256 `60034ceb199ebfed1b8e63a858c1a05c1331d68f81eaefadc27fa76b301f1c5c`.
- The HTA, help, and printed header all identify January 2018. Runtime identity remains `1601eq-v2018`.

The local January 2019 PDF (`5277addc...`) and guide (`64730a52...`) are incompatible comparators: the 2019 form adds Item 24 “Other Payments,” shifts the later printed items, and cannot supply the 2018 serialization contract.

## Field and control proof

The representative dummy save contains the initial six ATC rows. Source inspection proves up to 111 dynamic row positions. Because each added row contributes four serialized keys, the complete category-dependent source-reachable union is 621 fields. `fields.json` contains all 621 and hashes its ordered IDs to `4599618d0a2da5bace1ff7e721e4840df72d0d9ed596e3a21a6abb09d82e8bd0`.

`fixtures/runtime-control-inventory-v796.json` separately records 98 static controls (93 with IDs and five without) and a source-derived maximum of 654 runtime control instances. HTML-looking strings inside script blocks are excluded from this DOM inventory.

ATC row slots are positional within the selected Private/Government catalog, not permanent meanings. `AtcCode1` therefore does not identify one universal ATC across categories. The package catalog has 211 records that name 1601EQ: 111 Private, 96 Government, and four malformed empty-category records unreachable through the two UI choices.

## Validation and calculation proof

All 88 local HTA functions were inventoried. The ordered `validateForm` body supplies the 48 recorded input, change, Validate, and Save-preflight rules. Live UI observation confirmed the exact first two reachable dialogs:

- `Please enter a valid year on Item 1.`
- `Please select Quarter on Item 2`

The eight calculation records bind row withholding (`tax base × rate / 100`), Item 19, Item 24, Item 25, Item 29, and Item 30. `round()`/`formatCurrency()` use binary floating-point half-up formatting to two decimals. Malformed numeric strings become `0.00`; the shared numeric key filter permits more than one decimal point.

## ATC rate audit

For years through 2018, `changeATCRate` overrides catalog values for ATCs 650, 651, 663, and 710. For later years it uses `atcCodes.xml`. The 7.9.6 catalog conflicts with the official January 2019 form for at least these entries:

- Government WC651: package 32%, official 15%.
- Government WC710: package 20%, official 15%.
- WI661 and WC661: package 10%, official 15%.

The official January 2019 form also prints 15% for WI/WC650, WI/WC651, WI/WC661, WI/WC663, and WI/WC710. The rules preserve package behavior and mark these discrepancies as defects rather than silently correcting them.

## Workflow and serialization evidence

Save uses only TIN/branch nonblank, RDO-not-000, and name-nonblank checks. Full Validate is separate. Address lines are escaped, concatenated into one XML field, then split on reopen at encoded position 127. Reopen also replaces the saved email with the current global profile email.

Submit / Final Copy is submission-first. Source paths set `txtFinalFlag` to 1 on transport success, 2 on retry/failure, and 3 on connection-failure offline-final fallback. These post-confirmation branches were not executed.

## Repository comparison

The repository contains only a generic registry entry for `1601EQ`; no form-specific Rust validation/calculation model or HTML renderer was found. The registry marks `requires_employees: true`, while the official help applies to every withholding agent/payor required to withhold expanded taxes and does not require employees. This mismatch is reported only; no production code was changed.
