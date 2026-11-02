#!/usr/bin/env python3
"""Render the video's cards to JPEG stills. Results numbers are computed
from eval/results/full-1/runs.jsonl by code, never typed by hand.

Usage: video/.venv/bin/python video/cards.py  (PLAYWRIGHT_BROWSERS_PATH must
point at the downloaded browsers; video/build.sh sets it.)
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from playwright.sync_api import sync_playwright


def results_numbers() -> dict:
    recs = [json.loads(line) for line in
            open(ROOT / "eval" / "results" / "full-1" / "runs.jsonl")]
    cases = sorted({r["case"] for r in recs})
    runs_each = len([r for r in recs if r["case"] == cases[0]])
    faults = {"volume_full", "upload_dir_not_writable", "database_unavailable"}
    base_alarms = sum(1 for r in recs
                      if r["case"].startswith("H") and r["baseline_cause"] in faults)
    p3 = [r for r in recs if r["case"] == "P3"]
    sup = sum(r["citations_supported"] for r in recs)
    tot = sum(r["citations_total"] for r in recs)
    applied = [r for r in recs if r["repair_applied"]]
    return {
        "CASES": str(len(cases)),
        "RUNS": str(runs_each),
        "TOTAL": str(len(recs)),
        "MODEL_OK": str(sum(1 for r in recs if r["correct"])),
        "BASE_OK": str(sum(1 for r in recs if r["baseline_correct"])),
        "P3_MODEL": str(sum(1 for r in p3 if r["correct"])),
        "P3_BASE": str(sum(1 for r in p3 if r["baseline_correct"])),
        "ALARMS": str(sum(1 for r in recs if r.get("false_alarm"))),
        "BASE_ALARMS": str(base_alarms),
        "CITE_PCT": str(round(100 * sup / tot)),
        "REPAIRED": str(sum(1 for r in applied if r["recovery_verified"])),
        "REPAIRS": str(len(applied)),
    }


def main() -> None:
    out = ROOT / "video" / "out" / "cards"
    out.mkdir(parents=True, exist_ok=True)
    numbers = results_numbers()
    print("results card numbers:", numbers)
    template = (ROOT / "video" / "cards" / "results.html").read_text()
    for key, value in numbers.items():
        template = template.replace("{{" + key + "}}", value)
    assert "{{" not in template, "unfilled placeholder in results card"
    (out / "results.html").write_text(template)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        for name, src in (("title", ROOT / "video" / "cards" / "title.html"),
                          ("results", out / "results.html"),
                          ("close", ROOT / "video" / "cards" / "close.html")):
            page.goto(src.as_uri())
            page.wait_for_timeout(400)
            page.screenshot(path=str(out / f"{name}.jpg"), type="jpeg", quality=92)
            print("wrote", out / f"{name}.jpg")
        browser.close()


if __name__ == "__main__":
    main()
