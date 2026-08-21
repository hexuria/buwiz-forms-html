# buwiz-forms-html

HTML corpus for BIR forms. Layout edits are commits here. The product app
(`hexuria/buwiz-forms`) keeps only the **frozen** tree for fill/print.

| Tree | What it is |
| --- | --- |
| `forms/` | Stage 1 generated sheets (full generator batch, including `extra/`) |
| `forms-corrected/` | Stage 2 copy + declared corrections + fail-closed TIN `name=` stamps |
| `html-frozen/` | Product freeze: 43 inventory sheets the app actually loads. Tag `v1-frozen` |

No extractor, no `gate.py`, no Rust, no formgen CI. Do not invent a `frm…`
key. Do not copy leftover uniqueness onto `name=`. Do not commit saveXML.
