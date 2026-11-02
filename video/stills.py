#!/usr/bin/env python3
"""Framing stills for scenes 3, 4 and 6 at the recording geometry.

Drives the page's own functions with synthetic P3-like state (imported
from web/screenshots.py: no lab, no model) through record.py's own
framing code: PAGE_VIEWPORT, PAGE_ZOOM, SCENE4_FRAME_JS and the
click/park pointer helpers. Fails (exit 1) when a framing check fails.
Re-runnable; for framing review only. The video must still be a real take.

Usage: PLAYWRIGHT_BROWSERS_PATH=video/.browsers \\
       video/.venv/bin/python video/stills.py
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "video" / "out" / "stills"

from playwright.sync_api import sync_playwright

from video import record
from web.screenshots import ANSWER, CHECKS, DONE, REPAIR

CHECKS_PASSED = True


def check(ok, message):
    global CHECKS_PASSED
    print(("ok   " if ok else "FAIL ") + message, flush=True)
    CHECKS_PASSED = CHECKS_PASSED and bool(ok)


def rect(page, selector):
    return page.evaluate("""(sel) => {
        const el = document.querySelector(sel);
        return el ? el.getBoundingClientRect().toJSON() : null;
    }""", selector)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport=record.PAGE_VIEWPORT)
        page.add_init_script(record.POINTER_JS)
        page.goto((ROOT / "web" / "index.html").as_uri())
        page.wait_for_selector("#go")
        page.wait_for_selector("#take-pointer")
        page.evaluate(f"document.body.style.zoom = '{record.PAGE_ZOOM}%'")

        body_px = page.evaluate(
            "() => parseFloat(getComputedStyle(document.body).fontSize)"
            " * (parseFloat(document.body.style.zoom) / 100)")
        check(body_px >= 22, f"body text {body_px:.1f}px tall in the final frame")

        # Scene 3: investigating, pointer parked beside Investigate.
        # (No real click: that would start a live investigation.)
        for ev in CHECKS[:3]:
            page.evaluate("addCheck", ev)
        page.evaluate("setStep(1, 'current')")
        go = page.locator("#go").bounding_box()
        page.mouse.move(go["x"] + go["width"] / 2, go["y"] + go["height"] / 2,
                        steps=15)
        record.park_beside_box(page, go)
        time.sleep(0.4)
        ring = rect(page, "#take-pointer")
        check(ring["x"] >= go["x"] + go["width"],
              "scene 3 pointer parked beside Investigate, off its text")
        page.screenshot(path=str(OUT / "scene-3.png"))

        # Scene 4: report, citation clicked for real, framed by
        # record.py's own post-click code.
        page.evaluate("addCheck", CHECKS[3])
        page.evaluate("([done]) => { showReport(done); setStep(1, 'done'); "
                      "setStep(2, 'done'); setStep(3, 'current'); "
                      "document.getElementById('status').textContent = "
                      "finishedStatus(4, done.seconds, done.cost_usd); "
                      "document.getElementById('report-sec')"
                      ".scrollIntoView({block: 'start'}); }", [DONE])
        cite = page.locator("#findings .cite-link").first
        cite.evaluate("el => el.scrollIntoView({block: 'center'})")
        time.sleep(0.3)
        record.click_box(page, cite.bounding_box())
        page.evaluate(record.SCENE4_FRAME_JS)
        record.park_beside_box(page, cite.bounding_box())
        time.sleep(0.5)
        vw, vh = record.PAGE_VIEWPORT["width"], record.PAGE_VIEWPORT["height"]
        chip, mark = rect(page, ".cite-link.active"), rect(page, "mark.hit")
        inside = lambda r: (r["x"] >= 0 and r["y"] >= 0
                            and r["x"] + r["width"] <= vw
                            and r["y"] + r["height"] <= vh)
        check(chip and inside(chip), "scene 4 clicked claim visible with the evidence")
        check(mark and inside(mark), "scene 4 highlighted value visible with the claim")
        head = page.evaluate("""() => [...document.querySelectorAll('.panel.left h2')]
            .find((e) => e.textContent.trim() === 'Evidence')
            .getBoundingClientRect().toJSON()""")
        panel = rect(page, ".panel.left")
        cut = head["y"] < panel["y"] and head["y"] + head["height"] > panel["y"]
        check(not cut, "'Evidence' heading not half cut off at the top")
        page.screenshot(path=str(OUT / "scene-4.png"))

        # Scene 6: follow-up answered, repair verified, pointer parked
        # beside the result instead of on it.
        page.evaluate("([q, ev]) => { const box = startFollowup(q); "
                      "renderAnswer(box, ev); }",
                      ["What rules out a full disk?", ANSWER])
        page.evaluate("([r]) => { renderRepair(r); setStep(3, 'done'); "
                      "setStep(4, 'done'); "
                      "document.getElementById('repair-out')"
                      ".scrollIntoView({block: 'center'}); }", [REPAIR])
        record.park_beside_box(
            page, page.locator("#repair-out .verify-ok").bounding_box())
        time.sleep(0.4)
        ring, okbox = rect(page, "#take-pointer"), rect(page, "#repair-out .verify-ok")
        overlap = not (ring["x"] >= okbox["x"] + okbox["width"]
                       or ring["x"] + ring["width"] <= okbox["x"]
                       or ring["y"] >= okbox["y"] + okbox["height"]
                       or ring["y"] + ring["height"] <= okbox["y"])
        check(not overlap, "scene 6 pointer clear of the Verified block")
        page.screenshot(path=str(OUT / "scene-6.png"))
        browser.close()
    print("stills in", OUT)
    if not CHECKS_PASSED:
        sys.exit("framing checks FAILED")


if __name__ == "__main__":
    main()
