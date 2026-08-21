# Reviewed comb topology — operator guide

`REVIEWED_COMB_TOPOLOGY` in `audit.py` is a small, hand-maintained registry of
human-reviewed comb compartment counts. It exists for exactly one situation:
`check_comb_slots_match_printed`'s `printed_compartments` measured the source
PDF's own vector operators and could not decide, on its own evidence, how many
compartments the sheet prints inside one comb (`source-topology-unevaluable`
-- competing band/tone topologies, ambiguous U-frame ownership, no
strict-majority reading). That is a real limit of what vector data alone can
settle; the designed way past it is a human looking at the official page and
saying so, the same way `scripts/audit_html_form_migration.py`'s
trusted-producer registries are only ever populated after the user reviews
them.

**The registry ships EMPTY.** An empty registry changes nothing this file
already reports -- every currently-unevaluable subject stays unevaluable. Do
not add an entry without the user's explicit, per-subject confirmation.

## What the registry is, and is not

- It supplies a **compartment COUNT only.** It never supplies divider
  positions -- those stay unevaluable exactly as they do today, and
  `check_comb_slots_match_printed` reports `source topology was decided by
  the reviewed-comb-topology registry, which supplies only a compartment
  count, not divider positions` wherever a position comparison would
  otherwise have run.
- It is consulted **only** for a subject whose own verdict is already
  `source-topology-unevaluable` (specifically: `printed_compartments` raised
  for this exact cell). It is never consulted for, and must never contain an
  entry for, a subject the audit can decide from vector data on its own --
  that is an ERROR (`reviewed-comb-topology-invalid`), by design, so the
  registry can never be used to overrule a real disagreement between the
  audit and the layout/emission.
- A subject the registry decides is published with
  `layout_relation == "decided-by-review"`, and separately listed under the
  assertion's `decided_by_review_subjects`, so a reader of `build/audit.json`
  can always tell a human-reviewed fact apart from one the audit measured
  itself. It still participates in every other comparison exactly as a
  measured subject does (an emitted or lattice count that disagrees with the
  reviewed count is still an offender).

## Entry format

Each entry is keyed by `(slug, page, cell_id)` -- the form's slug (e.g.
`"1604cf-2008"`), the 1-based page number, and the layout cell id that owns
the comb (e.g. `"p2c73"`). The value is a plain dict; every one of these
fields is required, and a missing field is an ERROR, not a skipped entry:

| Field | Type | Meaning |
| --- | --- | --- |
| `compartments` | positive `int` | The reviewed printed compartment count. |
| `source_sha256` | 64-char lowercase hex `str` | The pinned source PDF's own `source.sha256` (from the form's `.ir.json`, key `source.sha256`). An entry whose sha256 no longer matches the current IR's is an ERROR -- it must be re-reviewed against the new document, never silently reused. |
| `page` | `int` | Must equal the key's own page (transcription check). |
| `cell_id` | `str` | Must equal the key's own cell_id (transcription check). |
| `bbox` | `[x0, y0, x1, y1]` | The comb cell's own rectangle, in PDF points. Must equal the active layout cell's own `x0`/`y0`/`x1`/`y1` within 1e-6pt -- if the cell has moved since review, the entry is stale and is refused. |
| `reviewer` | non-empty `str` | Who confirmed the count. |
| `date` | non-empty `str` | When it was confirmed (e.g. `"2026-08-13"`). |
| `citation` | non-empty `str` | A free-text pointer to the evidence -- which official crop was compared, and what was seen. |

## Adding a reviewed fact

1. Confirm the fact with the user first. Never add or change an entry on your
   own judgement; this registry exists precisely because the audit cannot
   decide the question itself.
2. Look up the subject's current `page`/`cell_id`/`bbox` in
   `build/layout/<slug>.layout.json` (the same cell the offender names), and
   its source PDF's `source.sha256` in `build/ir/<slug>.ir.json`.
3. Add one entry to `REVIEWED_COMB_TOPOLOGY` in `audit.py`, next to the
   registry's own definition, with all eight fields above.
4. Re-run `python3 audit.py --self-test` and a full
   `python3 audit.py --assertions-only` over the corpus. The subject you
   added should move from `source-topology-unevaluable` to
   `decided-by-review`; every other subject and every other assertion number
   must be unchanged.
5. This lands as a review-bundle commit exactly like the mechanism itself:
   `audit.py` is the locked judge, so keep the diff to the added entries (and
   their evidence comment, if you want one) and nothing else.

`tools/formgen/review/topology-review-meta.json`, where present, is scratch
review material (bboxes, emitted counts, and the audit's own `why` for each
of the 13 known offenders, plus a path to an official-page crop for visual
comparison) -- a convenient starting point for step 2, not itself part of the
registry and not committed.
