"""Reviewed ledger decisions: pure data, shape-validated, shipped empty.

R2b of the comb-referee campaign. The subject ledger's designed review paths
(`eligible-for-reviewed-resolution` for active-unresolved subjects whose
four-way evidence agrees, `explicit-transition-required` for retained ones)
have checkers on every side but no review INPUT anywhere. This module is that
input, and nothing else: two registries of decisions a named reviewer made,
each entry carrying its provenance, plus the shape validation both consumers
run before trusting a single byte of it.

Doctrine, unchanged from the reviewed-topology registry this mirrors:

* the registries ship EMPTY and grow only after the user reviews the
  evidence panels generated for each subject;
* a producer never certifies its own promotion -- lattice.py consumes an
  entry and publishes the transitioned state WITH a certificate naming it,
  and comb_referee.py independently validates that certificate against this
  module AND against its own current-run evidence (four-way agreement for a
  resolution, a TRUE source corroboration for a transition).  Review cannot
  overrule the paper: an entry the referee's own evidence contradicts is an
  ERROR, not a stronger review;
* this module carries no geometry and no measurement.  It is data with a
  schema, importable by producer and adjudicator alike without breaching the
  referee's independence, and its bytes join both attested closures.

`REVIEWED_LEDGER_RESOLUTIONS` -- (slug, page, cell_id) -> decision for an
ACTIVE_UNRESOLVED subject: the reviewer confirms the four-way-agreed comb is
the sheet's comb, and the subject resolves.

`REVIEWED_LEDGER_TRANSITIONS` -- (slug, page, legacy_cell_id) -> decision for
a RETAINED_UNRESOLVED subject: the reviewer confirms the suppressed legacy
comb claim, corroborated from the source (R2a), and names the transition.
`active_composite` keeps the subject in the ledger as the composite of its
mapped partition cells; `retired_proven_false` exists for a subject whose
partition is not real, and is expected to stay unused.
"""
from __future__ import annotations

from typing import Any

RESOLUTION_CRITERION = "reviewed-ledger-resolution-v1"
TRANSITION_CRITERION = "reviewed-ledger-transition-v1"
PERMITTED_TRANSITIONS = ("active_composite", "retired_proven_false")

REVIEWED_LEDGER_RESOLUTIONS: dict[tuple[str, int, str], dict[str, Any]] = {
    # Registered from the C4b review sitting (2026-08-15). Every entry was
    # four-way-agreed by machine before it reached the reviewer; the sitting
    # displayed each region on the official sheet (page locator + full-width
    # context, live inputs marked) and the reviewer approved all rows.
    ("0605-1999", 1, "p1c66"): {
        "subject_key": "p1@324.00,727.33,376.68,746.28",
        "source_sha256": "de04419766c59bf27fdeb854c0f7c3f98601900caa20630442e671e2313e536f",
        "four_way": {"lattice": 4, "audit": 4,
                     "emitted": 4, "referee": 4},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("0605-1999", 1, "p1c77"): {
        "subject_key": "p1@324.00,778.08,376.68,797.03",
        "source_sha256": "de04419766c59bf27fdeb854c0f7c3f98601900caa20630442e671e2313e536f",
        "four_way": {"lattice": 4, "audit": 4,
                     "emitted": 4, "referee": 4},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1600wp-2010", 1, "p1c1"): {
        "subject_key": "p1@39.59,104.28,151.56,121.80",
        "source_sha256": "6ea2ef0f6c84a68ef1c50ad63f4ff0e95a68258f52b62b98f305c861c8b75d55",
        "four_way": {"lattice": 8, "audit": 8,
                     "emitted": 8, "referee": 8},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1600wp-2010", 1, "p1c74"): {
        "subject_key": "p1@366.40,465.96,401.29,479.90",
        "source_sha256": "6ea2ef0f6c84a68ef1c50ad63f4ff0e95a68258f52b62b98f305c861c8b75d55",
        "four_way": {"lattice": 4, "audit": 4,
                     "emitted": 4, "referee": 4},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1604c-2018", 1, "p1c4"): {
        "subject_key": "p1@135.02,115.34,208.61,137.90",
        "source_sha256": "5239dd7f94bfc9d8a1b2e330e2da955bd5a0223762f41e95112d6b3822325dfd",
        "four_way": {"lattice": 4, "audit": 4,
                     "emitted": 4, "referee": 4},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    # ("1604cf-2008", 2, "p2c73") was approved in the 112-row sitting and
    # REVOKED by Sitting 2 DECISION A (2026-08-16): the compartment rule
    # (>24.5pt cuts the run; runs shorter than 2 are not combs) proved the
    # cell is a plain table cell crossed by column rules -- slots of 68.64pt
    # and 26.64pt cannot be character boxes -- so there is no comb subject
    # left for the entry to bind. The rule is corpus-wide; special-casing
    # this form to preserve the entry is forbidden.
    ("1604e-2018", 1, "p1c4"): {
        "subject_key": "p1@135.02,121.46,208.61,144.02",
        "source_sha256": "1db203442630c74ff4c95b509e204f542c5ba8fb1bd812440793e314ce709876",
        "four_way": {"lattice": 4, "audit": 4,
                     "emitted": 4, "referee": 4},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1606-2018", 1, "p1c70"): {
        "subject_key": "p1@382.03,382.49,551.62,400.73",
        "source_sha256": "374eca083888f36ae18612741d8473c61376db44cd281318def831c73dadabfe",
        "four_way": {"lattice": 12, "audit": 12,
                     "emitted": 12, "referee": 12},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1700-2018", 1, "p1c14"): {
        "subject_key": "p1@41.24,150.62,83.42,168.86",
        "source_sha256": "a5850f698c8d7aaf165d74bff0cd6547ca8f83eb76552c3bde490cbc3bbfea6c",
        "four_way": {"lattice": 3, "audit": 3,
                     "emitted": 3, "referee": 3},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1700-2018", 1, "p1c16"): {
        "subject_key": "p1@98.06,150.62,140.18,168.86",
        "source_sha256": "a5850f698c8d7aaf165d74bff0cd6547ca8f83eb76552c3bde490cbc3bbfea6c",
        "four_way": {"lattice": 3, "audit": 3,
                     "emitted": 3, "referee": 3},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1700-2018", 1, "p1c18"): {
        "subject_key": "p1@154.58,150.62,196.97,168.86",
        "source_sha256": "a5850f698c8d7aaf165d74bff0cd6547ca8f83eb76552c3bde490cbc3bbfea6c",
        "four_way": {"lattice": 3, "audit": 3,
                     "emitted": 3, "referee": 3},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1700-2018", 1, "p1c21"): {
        "subject_key": "p1@327.54,150.62,370.27,168.86",
        "source_sha256": "a5850f698c8d7aaf165d74bff0cd6547ca8f83eb76552c3bde490cbc3bbfea6c",
        "four_way": {"lattice": 3, "audit": 3,
                     "emitted": 3, "referee": 3},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1700-2018", 1, "p1c61"): {
        "subject_key": "p1@41.24,374.81,83.42,393.05",
        "source_sha256": "a5850f698c8d7aaf165d74bff0cd6547ca8f83eb76552c3bde490cbc3bbfea6c",
        "four_way": {"lattice": 3, "audit": 3,
                     "emitted": 3, "referee": 3},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1700-2018", 1, "p1c63"): {
        "subject_key": "p1@98.06,374.81,140.18,393.05",
        "source_sha256": "a5850f698c8d7aaf165d74bff0cd6547ca8f83eb76552c3bde490cbc3bbfea6c",
        "four_way": {"lattice": 3, "audit": 3,
                     "emitted": 3, "referee": 3},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1700-2018", 1, "p1c65"): {
        "subject_key": "p1@154.58,374.81,196.97,393.05",
        "source_sha256": "a5850f698c8d7aaf165d74bff0cd6547ca8f83eb76552c3bde490cbc3bbfea6c",
        "four_way": {"lattice": 3, "audit": 3,
                     "emitted": 3, "referee": 3},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1700-2018", 1, "p1c68"): {
        "subject_key": "p1@327.54,374.81,370.27,393.05",
        "source_sha256": "a5850f698c8d7aaf165d74bff0cd6547ca8f83eb76552c3bde490cbc3bbfea6c",
        "four_way": {"lattice": 3, "audit": 3,
                     "emitted": 3, "referee": 3},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1701-2018", 1, "p1c13"): {
        "subject_key": "p1@195.26,133.94,238.13,151.58",
        "source_sha256": "19be91d78258eb7c255f2615610db2739f10c378f8ac97adc0887c1bf40d1b2e",
        "four_way": {"lattice": 3, "audit": 3,
                     "emitted": 3, "referee": 3},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1701-2018", 1, "p1c15"): {
        "subject_key": "p1@252.77,133.94,296.45,151.58",
        "source_sha256": "19be91d78258eb7c255f2615610db2739f10c378f8ac97adc0887c1bf40d1b2e",
        "four_way": {"lattice": 3, "audit": 3,
                     "emitted": 3, "referee": 3},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1701-2018", 1, "p1c17"): {
        "subject_key": "p1@313.10,133.94,357.07,151.58",
        "source_sha256": "19be91d78258eb7c255f2615610db2739f10c378f8ac97adc0887c1bf40d1b2e",
        "four_way": {"lattice": 3, "audit": 3,
                     "emitted": 3, "referee": 3},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1701-2018", 2, "p2c11"): {
        "subject_key": "p2@257.69,106.94,300.89,124.58",
        "source_sha256": "19be91d78258eb7c255f2615610db2739f10c378f8ac97adc0887c1bf40d1b2e",
        "four_way": {"lattice": 3, "audit": 3,
                     "emitted": 3, "referee": 3},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1701-2018", 2, "p2c13"): {
        "subject_key": "p2@315.29,106.94,358.51,124.58",
        "source_sha256": "19be91d78258eb7c255f2615610db2739f10c378f8ac97adc0887c1bf40d1b2e",
        "four_way": {"lattice": 3, "audit": 3,
                     "emitted": 3, "referee": 3},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1701-2018", 2, "p2c9"): {
        "subject_key": "p2@200.33,106.94,243.17,124.58",
        "source_sha256": "19be91d78258eb7c255f2615610db2739f10c378f8ac97adc0887c1bf40d1b2e",
        "four_way": {"lattice": 3, "audit": 3,
                     "emitted": 3, "referee": 3},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1701a-2018", 1, "p1c16"): {
        "subject_key": "p1@28.68,152.78,70.22,171.02",
        "source_sha256": "8d492eabc6da2088cf9a55084488b192def5cc415048f607142c8bce1b72bfb8",
        "four_way": {"lattice": 3, "audit": 3,
                     "emitted": 3, "referee": 3},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1701a-2018", 1, "p1c18"): {
        "subject_key": "p1@84.26,152.78,126.50,171.02",
        "source_sha256": "8d492eabc6da2088cf9a55084488b192def5cc415048f607142c8bce1b72bfb8",
        "four_way": {"lattice": 3, "audit": 3,
                     "emitted": 3, "referee": 3},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1701a-2018", 1, "p1c20"): {
        "subject_key": "p1@140.66,152.78,183.26,171.02",
        "source_sha256": "8d492eabc6da2088cf9a55084488b192def5cc415048f607142c8bce1b72bfb8",
        "four_way": {"lattice": 3, "audit": 3,
                     "emitted": 3, "referee": 3},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1701a-2018", 1, "p1c22"): {
        "subject_key": "p1@197.57,152.78,269.54,171.02",
        "source_sha256": "8d492eabc6da2088cf9a55084488b192def5cc415048f607142c8bce1b72bfb8",
        "four_way": {"lattice": 5, "audit": 5,
                     "emitted": 5, "referee": 5},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1701a-2018", 1, "p1c23"): {
        "subject_key": "p1@298.25,152.78,342.31,171.02",
        "source_sha256": "8d492eabc6da2088cf9a55084488b192def5cc415048f607142c8bce1b72bfb8",
        "four_way": {"lattice": 3, "audit": 3,
                     "emitted": 3, "referee": 3},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1701a-2018", 2, "p2c118"): {
        "subject_key": "p2@28.56,719.86,69.62,738.10",
        "source_sha256": "8d492eabc6da2088cf9a55084488b192def5cc415048f607142c8bce1b72bfb8",
        "four_way": {"lattice": 3, "audit": 3,
                     "emitted": 3, "referee": 3},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1701a-2018", 2, "p2c120"): {
        "subject_key": "p2@84.14,719.86,125.54,738.10",
        "source_sha256": "8d492eabc6da2088cf9a55084488b192def5cc415048f607142c8bce1b72bfb8",
        "four_way": {"lattice": 3, "audit": 3,
                     "emitted": 3, "referee": 3},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1701a-2018", 2, "p2c122"): {
        "subject_key": "p2@140.06,719.86,181.94,738.10",
        "source_sha256": "8d492eabc6da2088cf9a55084488b192def5cc415048f607142c8bce1b72bfb8",
        "four_way": {"lattice": 3, "audit": 3,
                     "emitted": 3, "referee": 3},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1701a-2018", 2, "p2c124"): {
        "subject_key": "p2@196.49,719.86,273.05,738.10",
        "source_sha256": "8d492eabc6da2088cf9a55084488b192def5cc415048f607142c8bce1b72bfb8",
        "four_way": {"lattice": 5, "audit": 5,
                     "emitted": 5, "referee": 5},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1701a-2018", 2, "p2c125"): {
        "subject_key": "p2@301.13,719.86,344.47,738.10",
        "source_sha256": "8d492eabc6da2088cf9a55084488b192def5cc415048f607142c8bce1b72bfb8",
        "four_way": {"lattice": 3, "audit": 3,
                     "emitted": 3, "referee": 3},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1706-2018", 1, "p1c84"): {
        "subject_key": "p1@376.63,414.05,546.34,432.53",
        "source_sha256": "5237ba69d5fae6a26dceffc8f39dfcab32fe7d57081bfba74dcf5c5550c1afa3",
        "four_way": {"lattice": 12, "audit": 12,
                     "emitted": 12, "referee": 12},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1706-2018", 2, "p2c118"): {
        "subject_key": "p2@535.18,463.75,592.34,484.99",
        "source_sha256": "5237ba69d5fae6a26dceffc8f39dfcab32fe7d57081bfba74dcf5c5550c1afa3",
        "four_way": {"lattice": 4, "audit": 4,
                     "emitted": 4, "referee": 4},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1707-2021", 2, "p2c102"): {
        "subject_key": "p2@31.92,615.55,376.99,633.82",
        "source_sha256": "b6bc016f240a8d6233db6fb0065b72b31e75cb665affc2b96bcd2066e7ad257e",
        "four_way": {"lattice": 24, "audit": 24,
                     "emitted": 24, "referee": 24},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1707-2021", 2, "p2c36"): {
        "subject_key": "p2@536.14,281.69,594.48,299.93",
        "source_sha256": "b6bc016f240a8d6233db6fb0065b72b31e75cb665affc2b96bcd2066e7ad257e",
        "four_way": {"lattice": 4, "audit": 4,
                     "emitted": 4, "referee": 4},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1707-2021", 2, "p2c38"): {
        "subject_key": "p2@377.71,299.93,550.54,319.97",
        "source_sha256": "b6bc016f240a8d6233db6fb0065b72b31e75cb665affc2b96bcd2066e7ad257e",
        "four_way": {"lattice": 12, "audit": 12,
                     "emitted": 12, "referee": 12},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1707-2021", 2, "p2c44"): {
        "subject_key": "p2@31.92,344.45,594.48,362.69",
        "source_sha256": "b6bc016f240a8d6233db6fb0065b72b31e75cb665affc2b96bcd2066e7ad257e",
        "four_way": {"lattice": 39, "audit": 39,
                     "emitted": 39, "referee": 39},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1707-2021", 2, "p2c46"): {
        "subject_key": "p2@31.92,362.69,594.48,380.93",
        "source_sha256": "b6bc016f240a8d6233db6fb0065b72b31e75cb665affc2b96bcd2066e7ad257e",
        "four_way": {"lattice": 39, "audit": 39,
                     "emitted": 39, "referee": 39},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1707-2021", 2, "p2c48"): {
        "subject_key": "p2@31.92,380.93,594.48,399.29",
        "source_sha256": "b6bc016f240a8d6233db6fb0065b72b31e75cb665affc2b96bcd2066e7ad257e",
        "four_way": {"lattice": 39, "audit": 39,
                     "emitted": 39, "referee": 39},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1707-2021", 2, "p2c50"): {
        "subject_key": "p2@31.92,399.29,594.48,418.01",
        "source_sha256": "b6bc016f240a8d6233db6fb0065b72b31e75cb665affc2b96bcd2066e7ad257e",
        "four_way": {"lattice": 39, "audit": 39,
                     "emitted": 39, "referee": 39},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1707-2021", 2, "p2c56"): {
        "subject_key": "p2@31.92,441.65,204.17,460.39",
        "source_sha256": "b6bc016f240a8d6233db6fb0065b72b31e75cb665affc2b96bcd2066e7ad257e",
        "four_way": {"lattice": 12, "audit": 12,
                     "emitted": 12, "referee": 12},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1707-2021", 2, "p2c62"): {
        "subject_key": "p2@31.92,460.39,204.17,478.75",
        "source_sha256": "b6bc016f240a8d6233db6fb0065b72b31e75cb665affc2b96bcd2066e7ad257e",
        "four_way": {"lattice": 12, "audit": 12,
                     "emitted": 12, "referee": 12},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1707-2021", 2, "p2c68"): {
        "subject_key": "p2@31.92,478.75,204.17,496.99",
        "source_sha256": "b6bc016f240a8d6233db6fb0065b72b31e75cb665affc2b96bcd2066e7ad257e",
        "four_way": {"lattice": 12, "audit": 12,
                     "emitted": 12, "referee": 12},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1707-2021", 2, "p2c74"): {
        "subject_key": "p2@31.92,496.99,204.17,515.23",
        "source_sha256": "b6bc016f240a8d6233db6fb0065b72b31e75cb665affc2b96bcd2066e7ad257e",
        "four_way": {"lattice": 12, "audit": 12,
                     "emitted": 12, "referee": 12},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1707-2021", 2, "p2c87"): {
        "subject_key": "p2@31.92,560.23,376.99,579.07",
        "source_sha256": "b6bc016f240a8d6233db6fb0065b72b31e75cb665affc2b96bcd2066e7ad257e",
        "four_way": {"lattice": 24, "audit": 24,
                     "emitted": 24, "referee": 24},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1707-2021", 2, "p2c92"): {
        "subject_key": "p2@31.92,579.07,376.99,597.31",
        "source_sha256": "b6bc016f240a8d6233db6fb0065b72b31e75cb665affc2b96bcd2066e7ad257e",
        "four_way": {"lattice": 24, "audit": 24,
                     "emitted": 24, "referee": 24},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1707-2021", 2, "p2c97"): {
        "subject_key": "p2@31.92,597.31,376.99,615.55",
        "source_sha256": "b6bc016f240a8d6233db6fb0065b72b31e75cb665affc2b96bcd2066e7ad257e",
        "four_way": {"lattice": 24, "audit": 24,
                     "emitted": 24, "referee": 24},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1707a-2021", 1, "p1c11"): {
        "subject_key": "p1@89.66,133.70,147.38,151.94",
        "source_sha256": "5742d6bf0ca58c601f6c87e486984714a976c6a8b8c1bc6fb246b11fc87f08c3",
        "four_way": {"lattice": 4, "audit": 4,
                     "emitted": 4, "referee": 4},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1707a-2021", 1, "p1c35"): {
        "subject_key": "p1@17.64,246.86,463.18,265.10",
        "source_sha256": "5742d6bf0ca58c601f6c87e486984714a976c6a8b8c1bc6fb246b11fc87f08c3",
        "four_way": {"lattice": 31, "audit": 31,
                     "emitted": 31, "referee": 31},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1707a-2021", 1, "p1c37"): {
        "subject_key": "p1@536.02,246.86,594.48,265.10",
        "source_sha256": "5742d6bf0ca58c601f6c87e486984714a976c6a8b8c1bc6fb246b11fc87f08c3",
        "four_way": {"lattice": 4, "audit": 4,
                     "emitted": 4, "referee": 4},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1707a-2021", 2, "p2c15"): {
        "subject_key": "p2@88.94,185.42,146.54,204.62",
        "source_sha256": "5742d6bf0ca58c601f6c87e486984714a976c6a8b8c1bc6fb246b11fc87f08c3",
        "four_way": {"lattice": 4, "audit": 4,
                     "emitted": 4, "referee": 4},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1707a-2021", 2, "p2c21"): {
        "subject_key": "p2@88.94,204.62,146.54,223.94",
        "source_sha256": "5742d6bf0ca58c601f6c87e486984714a976c6a8b8c1bc6fb246b11fc87f08c3",
        "four_way": {"lattice": 4, "audit": 4,
                     "emitted": 4, "referee": 4},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1707a-2021", 2, "p2c27"): {
        "subject_key": "p2@88.94,223.94,146.54,243.14",
        "source_sha256": "5742d6bf0ca58c601f6c87e486984714a976c6a8b8c1bc6fb246b11fc87f08c3",
        "four_way": {"lattice": 4, "audit": 4,
                     "emitted": 4, "referee": 4},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1707a-2021", 2, "p2c33"): {
        "subject_key": "p2@88.94,243.14,146.54,262.82",
        "source_sha256": "5742d6bf0ca58c601f6c87e486984714a976c6a8b8c1bc6fb246b11fc87f08c3",
        "four_way": {"lattice": 4, "audit": 4,
                     "emitted": 4, "referee": 4},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1707a-2021", 2, "p2c65"): {
        "subject_key": "p2@88.94,448.75,146.54,468.07",
        "source_sha256": "5742d6bf0ca58c601f6c87e486984714a976c6a8b8c1bc6fb246b11fc87f08c3",
        "four_way": {"lattice": 4, "audit": 4,
                     "emitted": 4, "referee": 4},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1707a-2021", 2, "p2c70"): {
        "subject_key": "p2@88.94,468.07,146.54,487.27",
        "source_sha256": "5742d6bf0ca58c601f6c87e486984714a976c6a8b8c1bc6fb246b11fc87f08c3",
        "four_way": {"lattice": 4, "audit": 4,
                     "emitted": 4, "referee": 4},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1707a-2021", 2, "p2c75"): {
        "subject_key": "p2@88.94,487.27,146.54,506.59",
        "source_sha256": "5742d6bf0ca58c601f6c87e486984714a976c6a8b8c1bc6fb246b11fc87f08c3",
        "four_way": {"lattice": 4, "audit": 4,
                     "emitted": 4, "referee": 4},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1707a-2021", 2, "p2c80"): {
        "subject_key": "p2@88.94,506.59,146.54,526.27",
        "source_sha256": "5742d6bf0ca58c601f6c87e486984714a976c6a8b8c1bc6fb246b11fc87f08c3",
        "four_way": {"lattice": 4, "audit": 4,
                     "emitted": 4, "referee": 4},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1800-2018", 1, "p1c15"): {
        "subject_key": "p1@284.33,131.78,342.25,148.82",
        "source_sha256": "e2e837852680196d0e9aa9a513f55c7a6e4924493b5440548e46217166cc085e",
        "four_way": {"lattice": 3, "audit": 3,
                     "emitted": 3, "referee": 3},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1800-2018", 1, "p1c68"): {
        "subject_key": "p1@556.54,412.61,584.56,429.29",
        "source_sha256": "e2e837852680196d0e9aa9a513f55c7a6e4924493b5440548e46217166cc085e",
        "four_way": {"lattice": 2, "audit": 2,
                     "emitted": 2, "referee": 2},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1800-2018", 2, "p2c63"): {
        "subject_key": "p2@401.35,358.73,542.02,376.73",
        "source_sha256": "e2e837852680196d0e9aa9a513f55c7a6e4924493b5440548e46217166cc085e",
        "four_way": {"lattice": 10, "audit": 10,
                     "emitted": 10, "referee": 10},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1801-2018", 1, "p1c112"): {
        "subject_key": "p1@21.60,625.15,544.65,644.98",
        "source_sha256": "ec49207aab9b035d1913d41091b677d9df690e01b391ed2c2f4c34cf43a524c6",
        "four_way": {"lattice": 11, "audit": 11,
                     "emitted": 11, "referee": 11},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1801-2018", 1, "p1c31"): {
        "subject_key": "p1@21.60,259.58,291.65,277.85",
        "source_sha256": "ec49207aab9b035d1913d41091b677d9df690e01b391ed2c2f4c34cf43a524c6",
        "four_way": {"lattice": 11, "audit": 11,
                     "emitted": 11, "referee": 11},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1801-2018", 1, "p1c32"): {
        "subject_key": "p1@291.65,259.58,376.39,277.85",
        "source_sha256": "ec49207aab9b035d1913d41091b677d9df690e01b391ed2c2f4c34cf43a524c6",
        "four_way": {"lattice": 5, "audit": 5,
                     "emitted": 5, "referee": 5},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1801-2018", 1, "p1c33"): {
        "subject_key": "p1@376.39,259.58,588.70,277.85",
        "source_sha256": "ec49207aab9b035d1913d41091b677d9df690e01b391ed2c2f4c34cf43a524c6",
        "four_way": {"lattice": 11, "audit": 11,
                     "emitted": 11, "referee": 11},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1801-2018", 2, "p2c5"): {
        "subject_key": "p2@21.60,92.18,219.29,110.66",
        "source_sha256": "ec49207aab9b035d1913d41091b677d9df690e01b391ed2c2f4c34cf43a524c6",
        "four_way": {"lattice": 14, "audit": 14,
                     "emitted": 14, "referee": 14},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2000-ot-2018", 1, "p1c144"): {
        "subject_key": "p1@23.88,818.52,180.38,835.32",
        "source_sha256": "64d987ef79ed57005c1f13f8c9a5732bde2bf40f57dd4ec9f2067ef96c3c492d",
        "four_way": {"lattice": 10, "audit": 10,
                     "emitted": 10, "referee": 10},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2200s-2018", 1, "p1c29"): {
        "subject_key": "p1@462.22,200.78,585.10,215.54",
        "source_sha256": "2626eab0f2681bd811d12ae6ed60e177d1961e4f462be5ed453d674f3418671b",
        "four_way": {"lattice": 4, "audit": 4,
                     "emitted": 4, "referee": 4},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2200s-2018", 1, "p1c84"): {
        "subject_key": "p1@368.95,614.71,585.10,629.97",
        "source_sha256": "2626eab0f2681bd811d12ae6ed60e177d1961e4f462be5ed453d674f3418671b",
        "four_way": {"lattice": 14, "audit": 14,
                     "emitted": 14, "referee": 14},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2316-2021", 1, "p1c25"): {
        "subject_key": "p1@261.47,176.67,309.33,191.44",
        "source_sha256": "8e927e65b096d7a786ba7d36c55c28ee3de3546278880d9de8c11a91d1b48d60",
        "four_way": {"lattice": 4, "audit": 4,
                     "emitted": 4, "referee": 4},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2316-2021", 1, "p1c40"): {
        "subject_key": "p1@167.22,254.18,309.33,269.98",
        "source_sha256": "8e927e65b096d7a786ba7d36c55c28ee3de3546278880d9de8c11a91d1b48d60",
        "four_way": {"lattice": 11, "audit": 11,
                     "emitted": 11, "referee": 11},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2551m-2002", 1, "p1c75"): {
        "subject_key": "p1@260.76,766.42,284.54,785.07",
        "source_sha256": "f678be684558b8fb15a026b70a7c473f904fd07d49df64e0345fe1c0f81de71e",
        "four_way": {"lattice": 2, "audit": 2,
                     "emitted": 2, "referee": 2},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2551m-2002", 1, "p1c76"): {
        "subject_key": "p1@284.54,766.42,326.16,785.07",
        "source_sha256": "f678be684558b8fb15a026b70a7c473f904fd07d49df64e0345fe1c0f81de71e",
        "four_way": {"lattice": 4, "audit": 4,
                     "emitted": 4, "referee": 4},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2551m-2002", 1, "p1c82"): {
        "subject_key": "p1@286.08,785.07,326.16,804.45",
        "source_sha256": "f678be684558b8fb15a026b70a7c473f904fd07d49df64e0345fe1c0f81de71e",
        "four_way": {"lattice": 4, "audit": 4,
                     "emitted": 4, "referee": 4},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2551m-2002", 1, "p1c87"): {
        "subject_key": "p1@260.76,804.45,284.54,822.72",
        "source_sha256": "f678be684558b8fb15a026b70a7c473f904fd07d49df64e0345fe1c0f81de71e",
        "four_way": {"lattice": 2, "audit": 2,
                     "emitted": 2, "referee": 2},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2551m-2002", 1, "p1c88"): {
        "subject_key": "p1@284.54,804.45,326.16,822.72",
        "source_sha256": "f678be684558b8fb15a026b70a7c473f904fd07d49df64e0345fe1c0f81de71e",
        "four_way": {"lattice": 4, "audit": 4,
                     "emitted": 4, "referee": 4},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2552-2018", 1, "p1c28"): {
        "subject_key": "p1@465.58,223.70,594.28,241.94",
        "source_sha256": "c0a9d7cd44cf931fb939a010a265d05655b9a1d55d21841ecf0b590594b1742a",
        "four_way": {"lattice": 4, "audit": 4,
                     "emitted": 4, "referee": 4},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2553-1999", 1, "p1c80"): {
        "subject_key": "p1@258.84,779.14,282.62,797.79",
        "source_sha256": "e52f96fe48aba2890078f889930744a4e13a4defe1284aa9c5292e2c702a20e5",
        "four_way": {"lattice": 2, "audit": 2,
                     "emitted": 2, "referee": 2},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2553-1999", 1, "p1c81"): {
        "subject_key": "p1@282.62,779.14,324.24,797.79",
        "source_sha256": "e52f96fe48aba2890078f889930744a4e13a4defe1284aa9c5292e2c702a20e5",
        "four_way": {"lattice": 4, "audit": 4,
                     "emitted": 4, "referee": 4},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2553-1999", 1, "p1c87"): {
        "subject_key": "p1@284.16,797.79,324.24,817.17",
        "source_sha256": "e52f96fe48aba2890078f889930744a4e13a4defe1284aa9c5292e2c702a20e5",
        "four_way": {"lattice": 4, "audit": 4,
                     "emitted": 4, "referee": 4},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2553-1999", 1, "p1c92"): {
        "subject_key": "p1@258.84,817.17,282.62,835.44",
        "source_sha256": "e52f96fe48aba2890078f889930744a4e13a4defe1284aa9c5292e2c702a20e5",
        "four_way": {"lattice": 2, "audit": 2,
                     "emitted": 2, "referee": 2},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2553-1999", 1, "p1c93"): {
        "subject_key": "p1@282.62,817.17,324.24,835.44",
        "source_sha256": "e52f96fe48aba2890078f889930744a4e13a4defe1284aa9c5292e2c702a20e5",
        "four_way": {"lattice": 4, "audit": 4,
                     "emitted": 4, "referee": 4},
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
}

REVIEWED_LEDGER_TRANSITIONS: dict[tuple[str, int, str], dict[str, Any]] = {
    # Sitting 2, DECISION A (2026-08-16): the compartment rule proved these
    # two "2-slot combs" are plain table cells crossed by grid rules
    # (compartments of 70.80/156.72pt and 68.64/26.64pt -- the corpus's
    # only compartments beyond the 24.5pt census bound besides 1604F
    # p1c25's label box). The rule retires them through THIS path -- the
    # same reviewed transition the user already exercised 28 times --
    # because deleting their subjects would rewrite the frozen legacy
    # denominator. Their cells now emit plain region-cut inputs; the
    # criterion is the crossing-rule re-derivation the referee runs against
    # Poppler (the "dividers" outrun the comb band by 8x and 21x).
    ("2551m-2002", 2, "p2c13"): {
        "subject_key": "p2@22.56,92.64,250.08,104.40",
        "source_sha256":
            "f678be684558b8fb15a026b70a7c473f904fd07d49df64e0345fe1c0f81de71e",
        "transition": "active_composite",
        "suppression_criterion": "source-crossing-rule-not-comb-scoped-v1",
        "reviewer": "user",
        "date": "2026-08-16",
        "citation": "Sitting 2, DECISION A: ADOPT-RULE",
    },
    ("1604cf-2008", 2, "p2c73"): {
        "subject_key": "p2@174.48,220.32,269.76,237.12",
        "source_sha256":
            "877fbeee071752b2d9af72924647196e6dafa71a2412e74bc9f17897767cc2e7",
        "transition": "active_composite",
        "suppression_criterion": "source-crossing-rule-not-comb-scoped-v1",
        "reviewer": "user",
        "date": "2026-08-16",
        "citation": "Sitting 2, DECISION A: ADOPT-RULE",
    },
    # Registered from the C4b review sitting (2026-08-15). Every entry's
    # suppression claim was corroborated TRUE from the pinned source by the
    # R2a re-derivations before it reached the reviewer, and 1800-2018 p1c4
    # (the one FALSE of thirty) is deliberately absent, blocked on F234.
    ("0605-1999", 1, "p1c54"): {
        "subject_key": "p1@13.44,452.38,579.84,699.49",
        "source_sha256": "de04419766c59bf27fdeb854c0f7c3f98601900caa20630442e671e2313e536f",
        "transition": "active_composite",
        "suppression_criterion": "source-partition-edge-in-final-picture-v1",
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1600wp-2010", 1, "p1c0"): {
        "subject_key": "p1@18.00,104.28,594.00,159.89",
        "source_sha256": "6ea2ef0f6c84a68ef1c50ad63f4ff0e95a68258f52b62b98f305c861c8b75d55",
        "transition": "active_composite",
        "suppression_criterion": "source-partition-edge-in-final-picture-v1",
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1600wp-2010", 1, "p1c36"): {
        "subject_key": "p1@53.19,293.84,352.39,311.12",
        "source_sha256": "6ea2ef0f6c84a68ef1c50ad63f4ff0e95a68258f52b62b98f305c861c8b75d55",
        "transition": "active_composite",
        "suppression_criterion": "source-crossing-rule-not-comb-scoped-v1",
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1604cf-2008", 1, "p1c0"): {
        "subject_key": "p1@30.24,101.04,582.00,146.88",
        "source_sha256": "877fbeee071752b2d9af72924647196e6dafa71a2412e74bc9f17897767cc2e7",
        "transition": "active_composite",
        "suppression_criterion": "source-partition-edge-in-final-picture-v1",
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1604f-2018", 1, "p1c26"): {
        "subject_key": "p1@14.64,241.82,591.46,260.06",
        "source_sha256": "fc34de40dc7e6bc5f7a8cbc3feb5b170cca4bce4f0abd5b7b0dece4e9dd75c4d",
        "transition": "active_composite",
        "suppression_criterion": "source-partition-edge-in-final-picture-v1",
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1606-2018", 2, "p2c135"): {
        "subject_key": "p2@27.36,598.39,593.76,704.14",
        "source_sha256": "374eca083888f36ae18612741d8473c61376db44cd281318def831c73dadabfe",
        "transition": "active_composite",
        "suppression_criterion": "source-printed-caption-block-not-character-cells-v1",
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("1707a-2021", 1, "p1c38"): {
        "subject_key": "p1@17.64,265.10,594.48,275.93",
        "source_sha256": "5742d6bf0ca58c601f6c87e486984714a976c6a8b8c1bc6fb246b11fc87f08c3",
        "transition": "active_composite",
        "suppression_criterion": "source-partition-edge-in-final-picture-v1",
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2000-ot-2018", 2, "p2c45"): {
        "subject_key": "p2@137.90,304.97,250.01,319.25",
        "source_sha256": "64d987ef79ed57005c1f13f8c9a5732bde2bf40f57dd4ec9f2067ef96c3c492d",
        "transition": "active_composite",
        "suppression_criterion": "source-partition-edge-in-final-picture-v1",
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2000-ot-2018", 2, "p2c46"): {
        "subject_key": "p2@250.01,304.97,488.38,319.25",
        "source_sha256": "64d987ef79ed57005c1f13f8c9a5732bde2bf40f57dd4ec9f2067ef96c3c492d",
        "transition": "active_composite",
        "suppression_criterion": "source-partition-edge-in-final-picture-v1",
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2200a-2020", 1, "p1c115"): {
        "subject_key": "p1@16.32,847.08,595.32,897.72",
        "source_sha256": "c294bd45da56aa641f40ed5ed22b6c7c782860e84c2da6431c3340bd73194879",
        "transition": "active_composite",
        "suppression_criterion": "source-printed-caption-block-not-character-cells-v1",
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2200a-2020", 1, "p1c94"): {
        "subject_key": "p1@16.32,717.58,595.32,735.82",
        "source_sha256": "c294bd45da56aa641f40ed5ed22b6c7c782860e84c2da6431c3340bd73194879",
        "transition": "active_composite",
        "suppression_criterion": "source-printed-caption-block-not-character-cells-v1",
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2200an-2018", 2, "p2c0"): {
        "subject_key": "p2@18.00,48.96,450.91,107.18",
        "source_sha256": "832163890dc19297cd1f47004626aadcc840204b48124f3c788a31dd50fb5288",
        "transition": "active_composite",
        "suppression_criterion": "source-printed-caption-block-not-character-cells-v1",
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2200c-2018", 1, "p1c111"): {
        "subject_key": "p1@16.32,849.60,595.32,898.56",
        "source_sha256": "7b60d517ac6f3697e351aa89c124423d03dd7cac0961c4319b6507dd0ae64ce2",
        "transition": "active_composite",
        "suppression_criterion": "source-printed-caption-block-not-character-cells-v1",
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2200c-2018", 1, "p1c6"): {
        "subject_key": "p1@450.55,103.46,595.32,134.18",
        "source_sha256": "7b60d517ac6f3697e351aa89c124423d03dd7cac0961c4319b6507dd0ae64ce2",
        "transition": "active_composite",
        "suppression_criterion": "source-partition-edge-in-final-picture-v1",
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2200c-2018", 1, "p1c90"): {
        "subject_key": "p1@16.32,710.02,595.32,728.26",
        "source_sha256": "7b60d517ac6f3697e351aa89c124423d03dd7cac0961c4319b6507dd0ae64ce2",
        "transition": "active_composite",
        "suppression_criterion": "source-printed-caption-block-not-character-cells-v1",
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2200p-2020", 1, "p1c114"): {
        "subject_key": "p1@16.32,847.20,595.32,896.76",
        "source_sha256": "7bf29a28a93f45ae7af9ba344d4755540abd324137831d594bc623b4a0c06d2c",
        "transition": "active_composite",
        "suppression_criterion": "source-printed-caption-block-not-character-cells-v1",
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2200p-2020", 1, "p1c93"): {
        "subject_key": "p1@16.32,717.70,595.32,735.94",
        "source_sha256": "7bf29a28a93f45ae7af9ba344d4755540abd324137831d594bc623b4a0c06d2c",
        "transition": "active_composite",
        "suppression_criterion": "source-printed-caption-block-not-character-cells-v1",
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2200s-2018", 1, "p1c0"): {
        "subject_key": "p1@23.16,51.36,430.51,105.02",
        "source_sha256": "2626eab0f2681bd811d12ae6ed60e177d1961e4f462be5ed453d674f3418671b",
        "transition": "active_composite",
        "suppression_criterion": "source-printed-caption-block-not-character-cells-v1",
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2200t-2022", 2, "p2c0"): {
        "subject_key": "p2@18.00,21.36,450.91,79.56",
        "source_sha256": "cea195a413e5aa1ba94da957ed982c0f5f95fd31ad8fa89bc57ce8733dca52fb",
        "transition": "active_composite",
        "suppression_criterion": "source-printed-caption-block-not-character-cells-v1",
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2200t-2022", 3, "p3c0"): {
        "subject_key": "p3@18.00,22.56,450.91,80.64",
        "source_sha256": "cea195a413e5aa1ba94da957ed982c0f5f95fd31ad8fa89bc57ce8733dca52fb",
        "transition": "active_composite",
        "suppression_criterion": "source-printed-caption-block-not-character-cells-v1",
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2550m-2007", 1, "p1c16"): {
        "subject_key": "p1@28.80,136.32,582.72,162.00",
        "source_sha256": "9fb4101ace8c781436dac85df138a8fb9790775291affe2dada030c490d0d2b6",
        "transition": "active_composite",
        "suppression_criterion": "source-partition-edge-in-final-picture-v1",
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2550m-2007", 1, "p1c22"): {
        "subject_key": "p1@28.80,187.68,582.72,871.20",
        "source_sha256": "9fb4101ace8c781436dac85df138a8fb9790775291affe2dada030c490d0d2b6",
        "transition": "active_composite",
        "suppression_criterion": "source-partition-edge-in-final-picture-v1",
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2550m-2007", 1, "p1c6"): {
        "subject_key": "p1@28.80,117.12,582.72,136.32",
        "source_sha256": "9fb4101ace8c781436dac85df138a8fb9790775291affe2dada030c490d0d2b6",
        "transition": "active_composite",
        "suppression_criterion": "source-partition-edge-in-final-picture-v1",
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2550m-2007", 1, "p1c7"): {
        "subject_key": "p1@66.00,118.80,99.84,134.40",
        "source_sha256": "9fb4101ace8c781436dac85df138a8fb9790775291affe2dada030c490d0d2b6",
        "transition": "active_composite",
        "suppression_criterion": "source-partition-edge-in-final-picture-v1",
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2551m-2002", 1, "p1c10"): {
        "subject_key": "p1@22.56,187.68,589.44,213.36",
        "source_sha256": "f678be684558b8fb15a026b70a7c473f904fd07d49df64e0345fe1c0f81de71e",
        "transition": "active_composite",
        "suppression_criterion": "source-partition-edge-in-final-picture-v1",
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2551m-2002", 1, "p1c20"): {
        "subject_key": "p1@22.56,213.36,589.44,243.84",
        "source_sha256": "f678be684558b8fb15a026b70a7c473f904fd07d49df64e0345fe1c0f81de71e",
        "transition": "active_composite",
        "suppression_criterion": "source-partition-edge-in-final-picture-v1",
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2551m-2002", 1, "p1c30"): {
        "subject_key": "p1@22.56,309.84,589.44,822.72",
        "source_sha256": "f678be684558b8fb15a026b70a7c473f904fd07d49df64e0345fe1c0f81de71e",
        "transition": "active_composite",
        "suppression_criterion": "source-partition-edge-in-final-picture-v1",
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2553-1999", 1, "p1c25"): {
        "subject_key": "p1@20.24,210.48,591.76,242.40",
        "source_sha256": "e52f96fe48aba2890078f889930744a4e13a4defe1284aa9c5292e2c702a20e5",
        "transition": "active_composite",
        "suppression_criterion": "source-partition-edge-in-final-picture-v1",
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
    ("2553-1999", 1, "p1c35"): {
        "subject_key": "p1@20.24,308.07,591.76,835.44",
        "source_sha256": "e52f96fe48aba2890078f889930744a4e13a4defe1284aa9c5292e2c702a20e5",
        "transition": "active_composite",
        "suppression_criterion": "source-partition-edge-in-final-picture-v1",
        "reviewer": "uriah", "date": "2026-08-15",
        "citation": "C4b review sitting, all 112 rows approved",
    },
}


REVIEWED_UNEVALUABLE_EXCEPTIONS: dict[tuple[str, int, str], dict[str, Any]] = {
    # (slug, page, cell_id or legacy_cell_id): {
    #     "subject_key": ...,
    #     "source_sha256": ...,
    #     "reason": ...,     # the EXACT refusal string being excepted
    #     "evidence": ...,   # why the paper cannot decide it
    #     "reviewer": ..., "date": ..., "citation": ...,
    # }
    #
    # The third designed review path, and the narrowest. The other two say
    # "the reviewer confirms what the paper shows"; this one says "the
    # reviewer accepts that the paper CANNOT show it" -- for subjects whose
    # claim is true but unprovable from the sheet alone.
    #
    # An entry appears here ONLY for a subject the user has decided by name.
    # The corpus currently refuses four subjects; exactly one of them has
    # been put to review and decided (F234, 1800-2018 p1c4, "exception").
    # The other three (1604f-2018 p1c25 and p1c36, 2551m-2002 p2c13) are
    # NOT registered: they have never been through a review sitting, and a
    # producer that excepts its own undecidable subjects has certified its
    # own promotion. They stay blocking until reviewed or fixed.
    #
    # `reason` is load-bearing and is why this cannot become a blanket
    # silencer: comb_referee.py honours an exception ONLY while the live
    # refusal string still equals the one recorded here. If the measurement
    # drifts -- a fix lands, a pin moves, the paper is re-read -- the
    # exception no longer matches and becomes an ERROR, not a pass. An
    # exception excuses one named, measured, unchanged verdict and nothing
    # else.
    ("1800-2018", 1, "p1c4"): {
        "subject_key": "p1@186.62,97.46,584.56,119.30",
        "source_sha256":
            "e2e837852680196d0e9aa9a513f55c7a6e4924493b5440548e46217166cc085e",
        "reason": "ledger subject has no active topology for adjudication",
        "evidence":
            "The sheet cannot corroborate this subject's suppression, and "
            "the measurement says why. Of the corpus's 30 retained "
            "subjects, 29 corroborate; this one alone cannot, because its "
            "partition has no full-span vertical edge -- the item 2/3/4 "
            "header's middle band leaves label cells straddling the rules "
            "the sheet paints at x=342.19, 470.62 and 541.18 -- so "
            "corroboration falls back to the sole full-span horizontal "
            "edge at y=100.24, which the sheet leaves unpainted across the "
            "DN 010 column for 42.60pt of its 397.94pt width. The rules "
            "are drawn in four fragments (y 98.18..100.10, 100.58..109.82, "
            "110.06..116.30, 116.30..118.58) separated by 0.48pt and "
            "0.24pt of bare paper. No knockout was painted over them, so "
            "bridge_knockout_bites correctly declines rather than fails; "
            "and a collinear small-gap bridging clause was refuted by "
            "census -- it would close 698 corpus-wide gaps to join the 2 "
            "here. The claim is true and the paper cannot show it.",
        "reviewer": "user",
        "date": "2026-08-15",
        "citation": "F234; verdict 'DECISION 2 - F234: exception'",
    },
    ("1604f-2018", 1, "p1c36"): {
        "subject_key": "p1@271.49,289.85,591.46,309.89",
        "source_sha256":
            "fc34de40dc7e6bc5f7a8cbc3feb5b170cca4bce4f0abd5b7b0dece4e9dd75c4d",
        "reason": "referee: one or more source slabs have ambiguous topology",
        "evidence":
            "16 uniform ~14pt compartments; lattice, audit and emitter all "
            "count 16 and the subject is active_resolved in the ledger. The "
            "referee measured 93.54% of the band (6.959375 of 7.44pt) and "
            "matched all 16 anchors to within 0.002pt, then abstained over "
            "the remaining 0.48pt sliver -- the thickness of a horizontal "
            "rule crossing the comb -- where only 13 of the 16 verticals "
            "survive the crossing and the rule's own fragments read as "
            "unrecognised ink. The direct lever is the slab-ignore bound, "
            "which is the frozen POSITION_TOL_PT = 0.25 and stays frozen. "
            "The claim is true and the paper cannot prove it inside that "
            "sliver.",
        "reviewer": "user",
        "date": "2026-08-16",
        "citation": "Sitting 2, DECISION B: EXCEPTION",
    },
    ("1604f-2018", 1, "p1c25"): {
        "subject_key": "p1@14.64,223.58,591.46,241.82",
        "source_sha256":
            "fc34de40dc7e6bc5f7a8cbc3feb5b170cca4bce4f0abd5b7b0dece4e9dd75c4d",
        "reason": (
            "referee: chosen source topology lacks a clean single-frame "
            "subject proof"),
        "evidence":
            "p1c36's twin on the same sheet, decided the same way. All "
            "three implementations agree at 36 (lattice = audit = "
            "emitted); the form renders its 35 writable boxes correctly "
            "with the printed '7A ZIP Code' label box (compartment #32, "
            "72.75pt, the compartment rule's own evidence run cut) "
            "correctly skipped. The referee measured 99.97% of the band "
            "(7.4375 of 7.44pt) and matched all 35 dividers to within "
            "0.004pt, then abstained over one 0.48pt sliver -- the "
            "thickness of a horizontal rule crossing the comb -- where "
            "only 19 of the 35 verticals survive the crossing and no "
            "single-frame subject proof exists. A split into 31+4 "
            "run-scoped subjects was designed, then refuted: the sliver "
            "ambiguity lives inside the 31-run and survives any "
            "re-partition, and the count it would re-derive is not in "
            "dispute. The claim is true; the paper cannot prove it in "
            "that slab.",
        "reviewer": "user",
        "date": "2026-08-16",
        "citation": (
            "Sitting 3, DECISION C. Decided first under the user's "
            "delegation ('decide for yourself ... ill do final review and "
            "override'), then confirmed by the user's own pasted verdict: "
            "'SITTING 3 VERDICT / DECISION-C: EXCEPTION'"),
    },
}


def _entry_errors(key: Any, value: Any, kind: str) -> list[str]:
    errors: list[str] = []
    if (not isinstance(key, tuple) or len(key) != 3
            or not isinstance(key[0], str) or not key[0]
            or not isinstance(key[1], int) or key[1] < 1
            or not isinstance(key[2], str) or not key[2]):
        return [f"{kind} registry key is malformed: {key!r}"]
    label = f"{kind}:{key[0]}/p{key[1]}/{key[2]}"
    if not isinstance(value, dict):
        return [f"{label} entry is not a dict"]
    for field in ("subject_key", "source_sha256", "reviewer", "date",
                  "citation"):
        item = value.get(field)
        if not isinstance(item, str) or not item:
            errors.append(f"{label} {field} is missing or empty")
    sha = value.get("source_sha256")
    if isinstance(sha, str) and (
            len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha)):
        errors.append(f"{label} source_sha256 is not a lowercase sha256")
    if kind == "exception":
        for field in ("reason", "evidence"):
            item = value.get(field)
            if not isinstance(item, str) or not item:
                errors.append(f"{label} {field} is missing or empty")
        extra = set(value) - {
            "subject_key", "source_sha256", "reason", "evidence",
            "reviewer", "date", "citation"}
        if extra:
            errors.append(f"{label} carries unknown fields: {sorted(extra)}")
    elif kind == "resolution":
        four_way = value.get("four_way")
        if (not isinstance(four_way, dict)
                or set(four_way) != {"lattice", "audit", "emitted", "referee"}
                or not all(isinstance(item, int) and item >= 2
                           for item in four_way.values())
                or len(set(four_way.values())) != 1):
            errors.append(
                f"{label} four_way must be four equal counts >= 2")
        extra = set(value) - {
            "subject_key", "source_sha256", "four_way",
            "reviewer", "date", "citation"}
        if extra:
            errors.append(f"{label} carries unknown fields: {sorted(extra)}")
    else:
        if value.get("transition") not in PERMITTED_TRANSITIONS:
            errors.append(f"{label} transition is not permitted")
        criterion = value.get("suppression_criterion")
        if not isinstance(criterion, str) or not criterion:
            errors.append(f"{label} suppression_criterion is missing")
        extra = set(value) - {
            "subject_key", "source_sha256", "transition",
            "suppression_criterion", "reviewer", "date", "citation"}
        if extra:
            errors.append(f"{label} carries unknown fields: {sorted(extra)}")
    return errors


def registry_errors(
        resolutions: dict[Any, Any] | None = None,
        transitions: dict[Any, Any] | None = None,
        exceptions: dict[Any, Any] | None = None,
        ) -> list[str]:
    """Every shape defect in all three registries; empty ones are valid."""
    resolutions = (REVIEWED_LEDGER_RESOLUTIONS
                   if resolutions is None else resolutions)
    transitions = (REVIEWED_LEDGER_TRANSITIONS
                   if transitions is None else transitions)
    exceptions = (REVIEWED_UNEVALUABLE_EXCEPTIONS
                  if exceptions is None else exceptions)
    errors: list[str] = []
    for key, value in resolutions.items():
        errors.extend(_entry_errors(key, value, "resolution"))
    for key, value in transitions.items():
        errors.extend(_entry_errors(key, value, "transition"))
    for key, value in exceptions.items():
        errors.extend(_entry_errors(key, value, "exception"))
    overlap = set(resolutions) & set(transitions)
    if overlap:
        errors.append(
            "a subject may carry a resolution or a transition, never both: "
            f"{sorted(overlap)[:3]}")
    # An exception excuses an UNEVALUABLE verdict; a resolution or transition
    # asserts the paper decided it. Holding both is a contradiction.
    contradiction = set(exceptions) & (set(resolutions) | set(transitions))
    if contradiction:
        errors.append(
            "a subject cannot be both decided and excepted as undecidable: "
            f"{sorted(contradiction)[:3]}")
    return errors


def self_test() -> int:
    """The registry validation, proven able to fail."""
    assert registry_errors({}, {}) == []
    good_resolution = {
        ("0605-1999", 1, "p1c66"): {
            "subject_key": "p1@1,2,3,4",
            "source_sha256": "0" * 64,
            "four_way": {"lattice": 3, "audit": 3,
                         "emitted": 3, "referee": 3},
            "reviewer": "self-test", "date": "2026-08-14",
            "citation": "self-test",
        },
    }
    good_transition = {
        ("0605-1999", 1, "p1c54"): {
            "subject_key": "p1@1,2,3,4",
            "source_sha256": "0" * 64,
            "transition": "active_composite",
            "suppression_criterion": "source-partition-edge-in-final-picture-v1",
            "reviewer": "self-test", "date": "2026-08-14",
            "citation": "self-test",
        },
    }
    assert registry_errors(good_resolution, good_transition) == []
    import copy

    def broken(kind: str, mutate) -> None:
        resolutions = copy.deepcopy(good_resolution)
        transitions = copy.deepcopy(good_transition)
        mutate(resolutions if kind == "resolution" else transitions)
        found = registry_errors(resolutions, transitions)
        assert found, f"{kind} forgery was accepted: {mutate.__doc__}"

    def no_reviewer(registry):
        """empty reviewer"""
        next(iter(registry.values()))["reviewer"] = ""
    broken("resolution", no_reviewer)
    broken("transition", no_reviewer)

    def bad_sha(registry):
        """uppercase sha"""
        next(iter(registry.values()))["source_sha256"] = "A" * 64
    broken("resolution", bad_sha)

    def unequal_four_way(registry):
        """four-way disagreement smuggled in"""
        next(iter(registry.values()))["four_way"]["referee"] = 4
    broken("resolution", unequal_four_way)

    def one_slot(registry):
        """a one-compartment comb is no comb"""
        next(iter(registry.values()))["four_way"] = {
            "lattice": 1, "audit": 1, "emitted": 1, "referee": 1}
    broken("resolution", one_slot)

    def unknown_transition(registry):
        """invented transition"""
        next(iter(registry.values()))["transition"] = "retired_quietly"
    broken("transition", unknown_transition)

    def extra_field(registry):
        """unknown field"""
        next(iter(registry.values()))["evil"] = True
    broken("resolution", extra_field)
    broken("transition", extra_field)

    def bad_key(registry):
        """page zero"""
        value = next(iter(registry.values()))
        registry.clear()
        registry[("0605-1999", 0, "p1c66")] = value
    broken("resolution", bad_key)

    good_exception = {
        ("1800-2018", 1, "p1c4"): {
            "subject_key": "p1@1,2,3,4",
            "source_sha256": "0" * 64,
            "reason": "referee: the source does not corroborate",
            "evidence": "self-test",
            "reviewer": "self-test", "date": "2026-08-16",
            "citation": "self-test",
        },
    }
    assert registry_errors({}, {}, good_exception) == []

    def broken_exception(mutate) -> None:
        import copy as _copy
        registry = _copy.deepcopy(good_exception)
        mutate(registry)
        assert registry_errors({}, {}, registry), mutate.__doc__

    def no_reason(registry):
        """an exception with no named refusal excuses everything"""
        next(iter(registry.values()))["reason"] = ""
    broken_exception(no_reason)

    def no_evidence(registry):
        """an exception with no evidence is an assertion, not a review"""
        next(iter(registry.values()))["evidence"] = ""
    broken_exception(no_evidence)

    def exception_extra(registry):
        """unknown field"""
        next(iter(registry.values()))["override"] = True
    broken_exception(exception_extra)

    contradiction = registry_errors(
        good_resolution, {},
        {("0605-1999", 1, "p1c66"): dict(next(iter(good_exception.values())))})
    assert any("both decided and excepted" in error
               for error in contradiction), contradiction

    both = registry_errors(good_resolution, {
        ("0605-1999", 1, "p1c66"): dict(
            next(iter(good_transition.values())))})
    assert any("never both" in error for error in both)
    print("review_registry self-test: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(self_test())
