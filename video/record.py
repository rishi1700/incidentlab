#!/usr/bin/env python3
"""Drive one real take: the terminal scene in ttyd plus one live page
session (investigate, report, follow-up, repair). Writes
video/out/raw/{terminal,page}.webm and moments.json.

Fails (exit 1) when the take is unusable: wrong diagnosis, no clickable
citation, failed follow-up, or a repair that did not verify. Re-run
video/record.sh for another take; the lab is reset between takes.

Run under the video venv: video/.venv/bin/python video/record.py
"""
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "video" / "out" / "raw"

from playwright.sync_api import sync_playwright

VIEWPORT = {"width": 1920, "height": 1080}
TTYD_URL = "http://127.0.0.1:7681"
PAGE_URL = "http://127.0.0.1:8090"
TYPE_DELAY_MS = 70

TERMINAL_COMMANDS = [
    ("python3 lab/verify.py", 6.0),
    ("python3 lab/faults.py inject P3", 6.0),
    ("python3 lab/verify.py", 5.0),
    ("curl -s -o /dev/null -w '%{http_code}\\n' http://localhost:8080/health", 3.0),
]

# Init scripts run as statements: this must not be wrapped in an uninvoked
# function, or the ring is never created and no click shows a pointer.
POINTER_JS = """document.addEventListener('DOMContentLoaded', () => {
  const ring = document.createElement('div');
  ring.id = 'take-pointer';
  ring.style.cssText = 'position:fixed;width:24px;height:24px;margin:-12px 0 0 -12px;'
    + 'border:2px solid #e11d48;border-radius:50%;pointer-events:none;'
    + 'z-index:99999;opacity:0;transition:opacity .15s;';
  document.addEventListener('mousemove', (e) => {
    // position:fixed lives in the zoomed coordinate space while mouse
    // coordinates don't, so scale them down (150% zoom during takes).
    const z = (parseFloat(document.body.style.zoom) || 100) / 100;
    ring.style.left = (e.clientX / z) + 'px';
    ring.style.top = (e.clientY / z) + 'px';
    ring.style.opacity = '1';
  });
  document.addEventListener('mousedown', () => { ring.style.transform = 'scale(0.7)'; });
  document.addEventListener('mouseup', () => { ring.style.transform = 'scale(1)'; });
  document.body.appendChild(ring);
});"""


def fail(moments, message):
    (RAW / "moments.json").write_text(json.dumps(moments, indent=2))
    print(f"TAKE FAILED: {message}", flush=True)
    sys.exit(1)


def record_terminal(playwright, moments):
    browser = playwright.chromium.launch()
    context = browser.new_context(
        viewport=VIEWPORT, record_video_dir=str(RAW), record_video_size=VIEWPORT)
    page = context.new_page()
    t0 = time.monotonic()
    page.goto(TTYD_URL)
    page.wait_for_selector("canvas", timeout=30000)
    time.sleep(2.0)  # shell ready
    marks = {"t0": 0.0}
    for command, settle in TERMINAL_COMMANDS:
        page.keyboard.type(command, delay=TYPE_DELAY_MS)
        time.sleep(0.4)
        page.keyboard.press("Enter")
        time.sleep(settle)
        marks[command.split()[0] + "_done"] = round(time.monotonic() - t0, 2)
    time.sleep(1.5)
    marks["end"] = round(time.monotonic() - t0, 2)
    time.sleep(3.0)  # keep recording past the last moment
    path = page.video.path()
    context.close()
    browser.close()
    Path(path).rename(RAW / "terminal.webm")
    moments["terminal"] = marks
    print("terminal scene recorded", flush=True)


def record_page(playwright, moments):
    browser = playwright.chromium.launch()
    context = browser.new_context(
        viewport=VIEWPORT, record_video_dir=str(RAW), record_video_size=VIEWPORT)
    page = context.new_page()
    t0 = time.monotonic()

    def at():
        return round(time.monotonic() - t0, 2)

    def step_click(x, y):
        # Visible pointer travel so every click reads on video.
        page.mouse.move(x, y, steps=15)
        time.sleep(0.4)
        page.mouse.click(x, y)

    def move_click(selector):
        box = page.locator(selector).bounding_box(timeout=30000)
        step_click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

    try:
        page.add_init_script(POINTER_JS)
        page.goto(PAGE_URL)
        page.wait_for_selector("#go", timeout=30000)
        page.wait_for_selector("#take-pointer", timeout=10000)
        page.evaluate("document.body.style.zoom = '150%'")
        time.sleep(1.0)
        marks = {}

        move_click("#go")
        page.screenshot(path=str(RAW / "click-investigate.png"))
        marks["investigate_click"] = at()
        try:
            page.wait_for_function(
                "document.getElementById('status').textContent === 'done.'",
                timeout=600000)
        except Exception:
            fail(moments, "investigation did not finish: "
                 + page.locator("#status").inner_text())
        time.sleep(1.5)
        marks["report"] = at()
        cause = page.locator("#cause").inner_text()
        if cause != "upload_dir_not_writable":
            fail(moments, f"wrong diagnosis for the video: {cause!r}")

        # Paced to the narration: report on screen ~12 s, then the
        # citation click, the highlighted value held ~6 s, then questions.
        page.evaluate("document.getElementById('report-sec')"
                      ".scrollIntoView({block: 'start'})")
        time.sleep(12.0)
        item = page.locator("#findings li", has_text=re.compile("permission denied", re.I))
        if item.count() == 0:
            fail(moments, "no 'Permission denied' finding to click")
        cite = item.first.locator(".cite-link")
        if cite.count() == 0:
            fail(moments, "'Permission denied' finding has no clickable citation")
        cite.evaluate("el => el.scrollIntoView({block: 'center'})")
        time.sleep(0.6)
        box = cite.bounding_box(timeout=30000)
        step_click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.screenshot(path=str(RAW / "click-citation.png"))
        time.sleep(0.5)
        page.evaluate("""() => {
          const t = document.querySelector('mark.hit')
            || document.querySelector('details.flash');
          if (t) t.scrollIntoView({block: 'center'});
        }""")
        time.sleep(0.5)
        hit_visible = page.evaluate("""() => {
          const m = document.querySelector('mark.hit');
          if (!m) return false;
          const r = m.getBoundingClientRect();
          return r.bottom > 0 && r.top < window.innerHeight
            && r.right > 0 && r.left < window.innerWidth;
        }""")
        if not hit_visible:
            fail(moments, "citation highlight is not visible")
        time.sleep(6.0)
        marks["citation"] = at()
        page.locator("#questions").scroll_into_view_if_needed()
        time.sleep(2.0)
        marks["questions"] = at()

        page.locator("#question").scroll_into_view_if_needed()
        page.click("#question")
        page.keyboard.type("What rules out a full disk?", delay=TYPE_DELAY_MS)
        time.sleep(0.5)
        move_click("#ask")
        page.screenshot(path=str(RAW / "click-ask.png"))
        marks["ask"] = at()
        try:
            page.wait_for_function(
                "[...document.querySelectorAll('.qa p')].some("
                "p => p.textContent && !p.textContent.startsWith('thinking'))",
                timeout=600000)
        except Exception:
            fail(moments, "follow-up answer never arrived")
        answer = page.locator(".qa p").last.inner_text()
        if answer.startswith("failed"):
            fail(moments, f"follow-up failed: {answer}")
        time.sleep(3.0)
        marks["answer"] = at()

        page.evaluate("document.getElementById('repair-sec')"
                      ".scrollIntoView({block: 'center'})")
        time.sleep(1.0)
        if not page.locator("#repair-name").inner_text().strip():
            fail(moments, "no mapped repair shown")
        move_click("#approve")
        page.screenshot(path=str(RAW / "click-approve.png"))
        marks["approve"] = at()
        try:
            page.wait_for_function(
                "document.getElementById('repair-out').textContent.includes('Verification')",
                timeout=300000)
        except Exception:
            fail(moments, "verification result never appeared")
        if "FAILED" in page.locator("#repair-out").inner_text():
            fail(moments, "repair or verification reported FAILED")
        page.evaluate("document.getElementById('repair-out')"
                      ".scrollIntoView({block: 'center'})")
        time.sleep(3.0)
        marks["verify"] = at()
        time.sleep(3.0)  # keep recording past the last moment

        path = page.video.path()
        context.close()
        browser.close()
        Path(path).rename(RAW / "page.webm")
        moments["page"] = marks
        print("page session recorded", flush=True)
    except SystemExit:
        context.close()
        browser.close()
        raise


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    moments = {}
    with sync_playwright() as playwright:
        record_terminal(playwright, moments)
        record_page(playwright, moments)
    (RAW / "moments.json").write_text(json.dumps(moments, indent=2))
    print("take complete:", moments)


if __name__ == "__main__":
    main()
