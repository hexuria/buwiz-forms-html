#!/usr/bin/env python3
"""One-off Stage 2 TIN sitting: official crop beside the corrected comb.

Serves on 127.0.0.1:4191 (leave :4190 for `just review-serve` on forms/).
Crops and the page live under tmp/tin-stage2-sitting/ (gitignored). Verdicts
are stored in localStorage until the user copies them; nothing is committed
as an approval.

    python3 tools/formgen/review/serve_tin_sitting.py
"""
from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import pathlib
import shutil
import socketserver
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[3]
OUT = REPO / "tmp" / "tin-stage2-sitting"
CROPS = OUT / "crops"
HOST = "127.0.0.1"
PORT = 4191
DPI = 144
SCALE = DPI / 72.0
# Browsers paint 1pt as 96/72 CSS px. An iframe clip using SCALE (144/72) showed
# the Address row instead of the TIN. Corrected crops are PNGs at 144 DPI.
CSS_PX_PER_PT = 96.0 / 72.0
PAD_PT = 10.0

# `branch` follows Uriah's 2026-08-17 lock rule and nothing else: lock the five
# cells to 00000 only where the OFFICIAL sheet already pre-prints 000 in the
# branch box. Where the official box is blank, the five cells stay editable —
# these are payment and withholding forms whose filer must name a real branch,
# and a painted 00000 would silently credit the head office.
LOCKED = "locked"
EDITABLE = "editable"

SITES = [
    {
        "id": "C01",
        "title": "2550M Feb 2007 — whole TIN strip even 3-3-3-5, branch LOCKED 00000",
        "form": "2550m-2007",
        "branch": LOCKED,
        "html": "/forms-corrected/2550m-2007/index.html",
        "pdf": pathlib.Path.home() / "Downloads/forms/2550M/bir2550m.pdf",
        "sha256": "9fb4101ace8c781436dac85df138a8fb9790775291affe2dada030c490d0d2b6",
        "box": (66.00, 118.80, 213.12, 134.40),
        "page_pt": (612.0, 1008.0),
        "note": "Old 3-cell artwork is covered. New cells are even across the whole TIN. The official sheet PRE-PRINTS 000 in this branch box, so the branch shows 00000 and cannot be typed over.",
        "recommend": "approve",
        "recommend_notes": "Whole strip 66–213.12 kept; every digit cell ~9.5pt. Branch is five locked zeros because BIR's own artwork prints 000 there — that is the only reason it is locked.",
    },
    {
        "id": "C02",
        "title": "0605 1999 — whole TIN strip even 3-3-3-5, branch EDITABLE (5 cells)",
        "form": "0605-1999",
        "branch": EDITABLE,
        "html": "/forms-corrected/0605-1999/index.html",
        "pdf": pathlib.Path.home() / "Downloads/forms/0605/0605version1999_09.02.2022_copy.pdf",
        "sha256": "de04419766c59bf27fdeb854c0f7c3f98601900caa20630442e671e2313e536f",
        "box": (30.23, 246.12, 223.91, 265.08),
        "page_pt": (612.0, 936.0),
        "note": "The official branch box is EMPTY — two ticks, no printed 000 — so the branch is NOT locked. 0605 is the payment form: a filer whose branch code is not the head office must be able to type it, or the remittance is credited to branch 000. Harvested inventory is 0605-v2003 against 1999 artwork — that revision gap is declared in the record.",
        "recommend": "approve",
        "recommend_notes": "Whole strip 30.23–223.91 kept; every digit cell ~11.3pt. Branch is five EDITABLE cells, no value, no readonly — the official box prints no 000. HTA max_length 5 is a 2003 harvest against 1999 art — that gap stays declared.",
    },
    {
        "id": "C03",
        "title": "2551M 2002 — whole TIN strip even 3-3-3-5, branch EDITABLE (5 cells)",
        "form": "2551m-2002",
        "branch": EDITABLE,
        "html": "/forms-corrected/2551m-2002/index.html",
        "pdf": pathlib.Path.home() / "Downloads/forms/2551M/2551m.pdf",
        "sha256": "f678be684558b8fb15a026b70a7c473f904fd07d49df64e0345fe1c0f81de71e",
        "box": (59.76, 190.80, 207.36, 210.24),
        "page_pt": (612.0, 1008.0),
        "note": "The official sheet prints two ticks and no 000, so the branch is NOT locked and stays five editable cells. No harvested fields.json. Authority is the 2026-08-15 3-3-3-5 rule plus an honest in-repo gap.",
        "recommend": "approve",
        "recommend_notes": "Whole strip 59.76–207.36 kept; every digit cell ~9.53pt. Branch is five EDITABLE cells — official box prints no 000. No fields.json — honest gap, not a borrowed 2550M cite.",
    },
    {
        "id": "C04",
        "title": "2553 1999 — whole TIN strip even 3-3-3-5, branch EDITABLE (5 cells)",
        "form": "extra/2553-1999",
        "branch": EDITABLE,
        "html": "/forms-corrected/extra/2553-1999/index.html",
        "pdf": pathlib.Path.home() / "Downloads/forms/2553v1999/42792553.pdf",
        "sha256": "e52f96fe48aba2890078f889930744a4e13a4defe1284aa9c5292e2c702a20e5",
        "box": (57.84, 189.36, 205.44, 208.80),
        "page_pt": (612.0, 1008.0),
        "note": "The official branch box is empty — no printed 000 — so the branch is NOT locked and stays five editable cells.",
        "recommend": "approve",
        "recommend_notes": "Whole strip 57.84–205.44 kept; every digit cell ~9.53pt. Branch is five EDITABLE cells — official box prints no 000. HTA frm2553:txtBranchCode max_length 5.",
    },
    {
        "id": "C05",
        "title": "1600WP 2010 — PRIMARY TIN strip even 3-3-3-5, branch EDITABLE (5 cells)",
        "form": "extra/1600wp-2010",
        "branch": EDITABLE,
        "html": "/forms-corrected/extra/1600wp-2010/index.html",
        "pdf": pathlib.Path.home() / "Downloads/forms/1600WPv2010/1600WP p1ENCS.pdf",
        "sha256": "6ea2ef0f6c84a68ef1c50ad63f4ff0e95a68258f52b62b98f305c861c8b75d55",
        "box": (53.19, 139.19, 276.00, 156.72),
        "page_pt": (612.0, 936.0),
        "note": "The official branch box prints four compartments with NO pre-printed 000, so the branch is NOT locked and stays five editable cells. 1600WP is a withholding remittance sheet — the branch code has to be enterable. Must ship with C06 (agent TIN on the same page). Do not approve one without the other.",
        "recommend": "approve",
        "recommend_notes": "Whole primary strip 53.19–276 kept; every digit cell ~12.76pt. Branch is five EDITABLE cells — four official compartments, none pre-printed. Approve only with C06.",
    },
    {
        "id": "C06",
        "title": "1600WP 2010 — AGENT TIN strip even 3-3-3-5, branch EDITABLE (5 cells)",
        "form": "extra/1600wp-2010",
        "branch": EDITABLE,
        "html": "/forms-corrected/extra/1600wp-2010/index.html",
        "pdf": pathlib.Path.home() / "Downloads/forms/1600WPv2010/1600WP p1ENCS.pdf",
        "sha256": "6ea2ef0f6c84a68ef1c50ad63f4ff0e95a68258f52b62b98f305c861c8b75d55",
        "box": (257.17, 465.96, 401.29, 479.90),
        "page_pt": (612.0, 936.0),
        "note": "The official agent branch box prints four compartments with no ink inside, so the branch is NOT locked and stays five editable cells. No harvested agent-branch field_key. Neighbours were text; they are now even 3-cell combs in the same strip.",
        "recommend": "approve",
        "recommend_notes": "Whole agent strip 257.17–401.29 kept; every digit cell ~8.3pt. Branch is five EDITABLE cells — four official compartments, no ink inside. No agent branch key — honest gap. Approve only with C05.",
    },
    {
        "id": "C07",
        "title": "1604CF 2008 — whole TIN strip even 3-3-3-5, branch LOCKED 00000",
        "form": "extra/1604cf-2008",
        "branch": LOCKED,
        "html": "/forms-corrected/extra/1604cf-2008/index.html",
        "pdf": pathlib.Path.home() / "Downloads/forms/1604CF/1604-CF July 2008 ENCS final.pdf",
        "sha256": "877fbeee071752b2d9af72924647196e6dafa71a2412e74bc9f17897767cc2e7",
        "box": (58.56, 129.84, 250.80, 144.72),
        "page_pt": (612.0, 1008.0),
        "note": "The official artwork PRE-PRINTS 000 in the first three of four branch compartments. Those printed zeros are covered and reproduced as a locked 00000, which is why this site is locked and C02–C06 are not. No harvested fields.json.",
        "recommend": "approve",
        "recommend_notes": "Whole strip 58.56–250.80 kept; every digit cell ~10.97pt. Branch locked 00000 because the official sheet prints 000 in it. No fields.json — honest gap.",
    },
]


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def crop_official(site: dict) -> None:
    pdf = site["pdf"]
    digest = sha256(pdf)
    if digest != site["sha256"]:
        raise SystemExit(f"{site['id']}: {pdf} sha256 {digest} != {site['sha256']}")
    x0, y0, x1, y1 = site["box"]
    x = max(0, int(round((x0 - PAD_PT) * SCALE)))
    y = max(0, int(round((y0 - PAD_PT) * SCALE)))
    w = int(round((x1 - x0 + 2 * PAD_PT) * SCALE))
    h = int(round((y1 - y0 + 2 * PAD_PT) * SCALE))
    prefix = CROPS / site["id"]
    subprocess.run(
        ["pdftocairo", "-png", "-r", str(DPI), "-f", "1", "-l", "1",
         "-x", str(x), "-y", str(y), "-W", str(w), "-H", str(h),
         str(pdf), str(prefix)],
        check=True,
    )
    produced = prefix.parent / f"{prefix.name}-1.png"
    dest = CROPS / f"{site['id']}.png"
    produced.replace(dest)


def crop_corrected(site: dict) -> None:
    """Raster the corrected HTML at the same box the official PNG used.

    Playwright is 96 CSS-dpi; device_scale_factor 1.5 makes 1pt = 2px, matching
    the 144 DPI official crop.
    """
    from playwright.sync_api import sync_playwright

    html_path = REPO / "forms-corrected" / site["form"] / "index.html"
    if not html_path.is_file():
        raise SystemExit(f"{site['id']}: missing {html_path}")
    x0, y0, x1, y1 = site["box"]
    page_w, page_h = site["page_pt"]
    dsf = DPI / 96.0
    clip = {
        "x": max(0.0, (x0 - PAD_PT) * CSS_PX_PER_PT),
        "y": max(0.0, (y0 - PAD_PT) * CSS_PX_PER_PT),
        "width": (x1 - x0 + 2 * PAD_PT) * CSS_PX_PER_PT,
        "height": (y1 - y0 + 2 * PAD_PT) * CSS_PX_PER_PT,
    }
    dest = CROPS / f"{site['id']}-corrected.png"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(
            viewport={
                "width": int(round(page_w * CSS_PX_PER_PT)) + 8,
                "height": int(round(page_h * CSS_PX_PER_PT)) + 8,
            },
            device_scale_factor=dsf,
        )
        page.goto(html_path.as_uri(), wait_until="networkidle")
        page.screenshot(path=str(dest), clip=clip, type="png")
        browser.close()


def clip_style(site: dict) -> str:
    x0, y0, x1, y1 = site["box"]
    page_w, page_h = site["page_pt"]
    width = (x1 - x0 + 2 * PAD_PT) * SCALE
    height = (y1 - y0 + 2 * PAD_PT) * SCALE
    left = (x0 - PAD_PT) * SCALE
    top = (y0 - PAD_PT) * SCALE
    return (
        f"--clip-w:{width:.1f}px;--clip-h:{height:.1f}px;"
        f"--page-w:{page_w * SCALE:.1f}px;--page-h:{page_h * SCALE:.1f}px;"
        f"--shift-x:{-left:.1f}px;--shift-y:{-top:.1f}px"
    )


def write_page() -> None:
    cards = []
    for site in SITES:
        note = f'<p class="note">{site["note"]}</p>' if site["note"] else ""
        locked = site["branch"] == LOCKED
        caption = ("Corrected TIN (even 3-3-3-5, branch locked 00000 — official prints 000)"
                   if locked else
                   "Corrected TIN (even 3-3-3-5, branch 5 EDITABLE cells — official prints no 000)")
        rec = site["recommend"]
        rec_notes = site["recommend_notes"]
        approve_checked = " checked" if rec == "approve" else ""
        reject_checked = " checked" if rec == "reject" else ""
        cards.append(f"""
<section class="site" data-id="{site['id']}">
  <h2>{site['id']} — {site['title']}</h2>
  {note}
  <p class="rec">Agent recommendation: <strong>{rec.upper()}</strong>. Flip the radio if you disagree — your click is the verdict.</p>
  <p><a href="{site['html']}" target="_blank" rel="noopener">Open the full corrected form</a></p>
  <div class="pair">
    <figure>
      <img src="crops/{site['id']}.png" alt="official {site['id']} crop">
      <figcaption>Official artwork (old printed TIN)</figcaption>
    </figure>
    <figure>
      <img src="crops/{site['id']}-corrected.png" alt="corrected {site['id']} crop">
      <figcaption>{caption}</figcaption>
    </figure>
  </div>
  <fieldset>
    <legend>Your verdict (you have the last say)</legend>
    <label><input type="radio" name="{site['id']}" value="approve"{approve_checked}> Approve</label>
    <label><input type="radio" name="{site['id']}" value="reject"{reject_checked}> Reject</label>
    <textarea name="{site['id']}-notes" rows="3" placeholder="verbatim notes, required on reject">{rec_notes}</textarea>
  </fieldset>
</section>
""")
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>TIN Stage 2 sitting — 7 sites</title>
<style>
  body {{ font: 15px/1.4 ui-sans-serif, system-ui, sans-serif; margin: 24px; color: #111; }}
  h1 {{ font-size: 22px; }}
  .law {{ background: #fff7e0; border: 1px solid #e0c060; padding: 12px 16px; max-width: 920px; }}
  .site {{ border-top: 2px solid #222; margin-top: 32px; padding-top: 16px; }}
  .pair {{ display: flex; gap: 24px; flex-wrap: wrap; align-items: flex-start; }}
  figure {{ margin: 0; }}
  figcaption {{ font-size: 13px; color: #444; margin-top: 6px; }}
  img {{ display: block; background: #fff; border: 1px solid #ccc; }}
  .clip {{ width: var(--clip-w); height: var(--clip-h); overflow: hidden; border: 1px solid #ccc; background: #fff; position: relative; }}
  .clip iframe {{ border: 0; position: absolute; left: var(--shift-x); top: var(--shift-y); width: var(--page-w); height: var(--page-h); }}
  .note {{ background: #eef4ff; border-left: 4px solid #245; padding: 8px 12px; }}
  .rec {{ background: #e8f6e8; border-left: 4px solid #185; padding: 8px 12px; }}
  fieldset {{ margin-top: 12px; max-width: 640px; }}
  textarea {{ width: 100%; margin-top: 8px; }}
  #dump {{ width: 100%; min-height: 160px; font-family: ui-monospace, monospace; }}
  #confirm {{ margin-left: 8px; }}
</style>
</head>
<body>
<h1>TIN Stage 2 sitting — 7 census sites</h1>
<p class="law">Non-regression only. These corrected TINs <strong>diverge from the official artwork on purpose</strong>.
Approve means “the whole old TIN strip is now even 3-3-3-5, and the branch group follows the lock rule”.
<strong>The lock rule:</strong> the last five cells are locked to 00000 <em>only</em> where the official sheet already
pre-prints 000 in the branch box — that is <strong>C01 (2550M) and C07 (1604CF) and nobody else</strong>.
Where the official branch box is blank, the five cells are <strong>editable</strong> (C02 0605, C03 2551M, C04 2553,
C05 and C06 1600WP): those are payment and withholding sheets whose filer must enter a real branch code,
and a painted 00000 would credit every remittance to the head office.
Reject names what is wrong. Status stays <code>declared</code> until <strong>you</strong> approve.
Radios are prefilled as an agent recommendation. That is not your verdict. Flip any site, then press <em>These are my verdicts</em>.
C05 and C06 are one form: do not split them.</p>
{''.join(cards)}
<h2>Recorded verdicts</h2>
<p>Prefill is the agent recommendation. It becomes yours only after you confirm.</p>
<textarea id="dump" readonly></textarea>
<button type="button" id="copy">Copy JSON</button>
<button type="button" id="confirm">These are my verdicts</button>
<script>
const ids = {json.dumps([s["id"] for s in SITES])};
const recommendations = {json.dumps({s["id"]: {"verdict": s["recommend"], "notes": s["recommend_notes"]} for s in SITES})};
const key = "tin-stage2-sitting-verdicts";
function read() {{
  const out = {{}};
  for (const id of ids) {{
    const verdict = (document.querySelector('input[name="'+id+'"]:checked') || {{}}).value || null;
    const notes = (document.querySelector('textarea[name="'+id+'-notes"]') || {{}}).value || "";
    out[id] = {{verdict, notes}};
  }}
  return out;
}}
function writeDump(confirmed) {{
  const stored = (() => {{
    try {{ return JSON.parse(localStorage.getItem(key) || "{{}}"); }} catch (e) {{ return {{}}; }}
  }})();
  const mine = confirmed || stored.reviewed_by === "Uriah";
  const payload = {{
    sitting: "TIN Stage 2",
    url: location.href,
    source: mine ? "uriah_verdict" : "agent_recommendation_pending_uriah",
    reviewed_by: mine ? "Uriah" : null,
    agent_recommendation: recommendations,
    verdicts: read()
  }};
  document.getElementById("dump").value = JSON.stringify(payload, null, 2);
  localStorage.setItem(key, document.getElementById("dump").value);
}}
function restore() {{
  const raw = localStorage.getItem(key);
  if (!raw) return;
  try {{
    const parsed = JSON.parse(raw);
    if (parsed.source !== "uriah_verdict") return;
    for (const [id, row] of Object.entries(parsed.verdicts || {{}})) {{
      if (row.verdict) {{
        const input = document.querySelector('input[name="'+id+'"][value="'+row.verdict+'"]');
        if (input) input.checked = true;
      }}
      const ta = document.querySelector('textarea[name="'+id+'-notes"]');
      if (ta && row.notes) ta.value = row.notes;
    }}
  }} catch (e) {{}}
}}
document.querySelectorAll("input, textarea").forEach(el => el.addEventListener("input", () => writeDump(false)));
document.getElementById("copy").addEventListener("click", () => {{
  writeDump(false);
  navigator.clipboard.writeText(document.getElementById("dump").value);
}});
document.getElementById("confirm").addEventListener("click", () => {{
  writeDump(true);
  navigator.clipboard.writeText(document.getElementById("dump").value);
}});
restore();
writeDump(false);
</script>
</body>
</html>
"""
    (OUT / "index.html").write_text(html, encoding="utf-8")


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(REPO), **kwargs)

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--skip-crops", action="store_true")
    args = parser.parse_args()
    if not (REPO / "forms-corrected").is_dir():
        sys.exit("forms-corrected/ is missing; apply the ledger first")
    OUT.mkdir(parents=True, exist_ok=True)
    CROPS.mkdir(parents=True, exist_ok=True)
    if not args.skip_crops:
        for site in SITES:
            print(f"crop {site['id']} from {site['pdf'].name}", file=sys.stderr)
            crop_official(site)
            print(f"crop {site['id']} corrected HTML", file=sys.stderr)
            crop_corrected(site)
    write_page()
    sitting = OUT / "index.html"
    class SittingHandler(Handler):
        def translate_path(self, path: str) -> str:
            if path in ("", "/", "/index.html"):
                return str(sitting)
            if path.startswith("/crops/"):
                return str(CROPS / path[len("/crops/"):])
            return super().translate_path(path)

    print(f"sitting: http://{HOST}:{args.port}/", file=sys.stderr)
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer((HOST, args.port), SittingHandler) as httpd:
        httpd.serve_forever()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
