# ARCHIVED

This generator ran once, on 2026-07-29, to produce `forms/`. From that point the
HTML in `forms/` is the maintained artefact and is edited by hand.

**Do not re-run this over an edited bundle.** It regenerates from the source PDF
and would discard manual edits. Each bundle's `provenance.json` records its
source file, SHA-256 and the generator version, so provenance survives without
re-deriving anything.

Kept in the tree as the audit trail for how `forms/` was derived. Not wired into
CI. See `README.md` for the method and why it replaced raster pixel-diffing.

Pipeline: `extract.py` -> `lattice.py` -> `fonts.py` -> `emit.py`, driven by
`batch.py`; `verify.py` proves a bundle by printing it to PDF and diffing the
re-extracted IR against the source IR.
