# buwiz-forms-html

HTML corpus and generator (`tools/formgen`) for BIR forms. The product app
(`hexuria/buwiz-forms`) keeps only `html-frozen/` for fill/print.

| Tree | What it is |
| --- | --- |
| `forms/` | Stage 1 generated sheets |
| `forms-corrected/` | Stage 2 copy + declared corrections + fail-closed TIN `name=` stamps |
| `html-frozen/` | Product freeze: 43 inventory sheets the app loads. Tag `v1-frozen` |
| `tools/formgen/` | Extractor, emitter, gate, corrections ledger. Operator-run `gate.py` still needs pinned PDFs. |

CI workflow is `formgen`. Do not invent a `frm…` key. Do not copy leftover
uniqueness onto `name=`. Do not commit saveXML.
