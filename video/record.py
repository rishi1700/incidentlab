#!/usr/bin/env python3
"""Drive one real take: the terminal scene in ttyd plus one live page
session (investigate, report, follow-up, repair). Writes
video/out/raw/{terminal,page}.webm and moments.json.

Fails (exit 1) when the take is unusable: wrong diagnosis, no clickable
citation, failed follow-up, or a repair that did not verify. Re-run
video/record.sh for another take; the lab is reset between takes.

Run under the video venv: video/.venv/bin/python video/record.py
With --skip-terminal, the ttyd scene is skipped (record.sh
--manual-terminal): only the page session is recorded, and moments.json
has no "terminal" entry.
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
# The page is recorded at the size it occupies in the final frame (above
# the 150px caption strip), so assemble.py never scales it. 115% zoom
# puts body text at 20px * 1.15 = 23px tall in the final 1080p frame.
PAGE_VIEWPORT = {"width": 1920, "height": 930}
PAGE_ZOOM = 115
PARK_GAP = 40
PARK_MAX_X = 1900  # keeps the pointer fully inside the frame
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
# function, or the pointer is never created and no click shows a pointer.
# A standard arrow pointer (black with a thin white outline, tip at the
# mouse point), gliding to each target over about half a second with a
# small press dip on every click.
POINTER_JS = """document.addEventListener('DOMContentLoaded', () => {
  const ptr = document.createElement('div');
  ptr.id = 'take-pointer';
  ptr.style.cssText = 'position:fixed;width:28px;height:28px;margin:-5px 0 0 -7px;'
    + 'pointer-events:none;z-index:99999;opacity:0;transform-origin:7px 5px;'
    + 'transition:left .5s ease-out,top .5s ease-out,opacity .15s,transform .12s ease-out;';
  ptr.innerHTML = '<svg width="28" height="28" viewBox="0 0 24 24">'
    + '<path d="M6 4 L6 17 L10.2 13 L12.2 18.2 L14.6 17.2 L12.6 12 L17.2 12 Z"'
    + ' fill="black" stroke="white" stroke-width="1.6" stroke-linejoin="round"/></svg>';
  document.addEventListener('mousemove', (e) => {
    // position:fixed lives in the zoomed coordinate space while mouse
    // coordinates don't, so scale them down for the PAGE_ZOOM body zoom.
    const z = (parseFloat(document.body.style.zoom) || 100) / 100;
    ptr.style.left = (e.clientX / z) + 'px';
    ptr.style.top = (e.clientY / z) + 'px';
    ptr.style.opacity = '1';
  });
  let pressTimer = 0;
  document.addEventListener('mousedown', () => {
    clearTimeout(pressTimer);
    ptr.style.transform = 'scale(0.85)';
  });
  document.addEventListener('mouseup', () => {
    clearTimeout(pressTimer);
    pressTimer = setTimeout(() => { ptr.style.transform = 'scale(1)'; }, 150);
  });
  document.body.appendChild(ptr);
});"""


def fail(moments, message):
    (RAW / "moments.json").write_text(json.dumps(moments, indent=2))
    print(f"TAKE FAILED: {message}", flush=True)
    sys.exit(1)


def click_box(page, box):
    # Visible pointer travel so every click reads on video. The glide
    # takes about half a second; wait for it to arrive before clicking.
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y, steps=15)
    time.sleep(0.6)
    page.mouse.click(x, y)


def park_beside_box(page, box, gap=PARK_GAP):
    """Park the pointer next to the element, off its text."""
    page.mouse.move(min(box["x"] + box["width"] + gap, PARK_MAX_X),
                    box["y"] + box["height"] / 2, steps=8)


def move_click(page, selector):
    """Click an element's centre, then park beside it. Returns its box."""
    box = page.locator(selector).bounding_box(timeout=30000)
    click_box(page, box)
    park_beside_box(page, box)
    return box


# After the scene-4 citation click: keep the clicked claim centred in
# the right panel and the highlighted value centred in the left panel,
# then nudge the left panel so the "Evidence" heading is never left
# half cut off at the top.
SCENE4_FRAME_JS = """() => {
  const chip = document.querySelector('.cite-link.active');
  if (chip) chip.scrollIntoView({block: 'center', inline: 'nearest'});
  const mark = document.querySelector('mark.hit')
    || document.querySelector('details.flash');
  if (mark) mark.scrollIntoView({block: 'center', inline: 'nearest'});
  const panel = document.querySelector('.panel.left');
  const head = [...panel.querySelectorAll('h2')]
    .find((e) => e.textContent.trim() === 'Evidence');
  if (head) {
    const pr = panel.getBoundingClientRect(), hr = head.getBoundingClientRect();
    if (hr.top < pr.top && hr.bottom > pr.top) panel.scrollTop -= (pr.top - hr.top);
  }
}"""


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
        viewport=PAGE_VIEWPORT, record_video_dir=str(RAW),
        record_video_size=PAGE_VIEWPORT)
    page = context.new_page()
    t0 = time.monotonic()

    def at():
        return round(time.monotonic() - t0, 2)

    try:
        page.add_init_script(POINTER_JS)
        page.goto(PAGE_URL)
        page.wait_for_selector("#go", timeout=30000)
        page.wait_for_selector("#take-pointer", timeout=10000)
        page.evaluate(f"document.body.style.zoom = '{PAGE_ZOOM}%'")
        time.sleep(1.0)
        marks = {}

        move_click(page, "#go")
        page.screenshot(path=str(RAW / "click-investigate.png"))
        marks["investigate_click"] = at()
        try:
            page.wait_for_function(
                "document.getElementById('status').textContent.startsWith('Finished:')",
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
        click_box(page, cite.bounding_box(timeout=30000))
        page.evaluate(SCENE4_FRAME_JS)
        park_beside_box(page, cite.bounding_box(timeout=30000))
        page.screenshot(path=str(RAW / "click-citation.png"))
        time.sleep(0.5)
        framed = page.evaluate("""() => {
          const visible = (el) => {
            if (!el) return false;
            const r = el.getBoundingClientRect();
            return r.bottom > 0 && r.top < window.innerHeight
              && r.right > 0 && r.left < window.innerWidth;
          };
          return {chip: visible(document.querySelector('.cite-link.active')),
                  mark: visible(document.querySelector('mark.hit'))};
        }""")
        if not framed["mark"]:
            fail(moments, "citation highlight is not visible")
        if not framed["chip"]:
            fail(moments, "clicked claim scrolled out of view")
        time.sleep(6.0)
        marks["citation"] = at()
        page.locator("#questions").scroll_into_view_if_needed()
        time.sleep(2.0)
        marks["questions"] = at()

        page.locator("#question").scroll_into_view_if_needed()
        page.click("#question")
        page.keyboard.type("What rules out a full disk?", delay=TYPE_DELAY_MS)
        time.sleep(0.5)
        move_click(page, "#ask")
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
        move_click(page, "#approve")
        page.screenshot(path=str(RAW / "click-approve.png"))
        marks["approve"] = at()
        try:
            page.wait_for_function(
                "document.getElementById('repair-out').textContent.includes('Verified:')"
                " || document.getElementById('repair-out').textContent.includes('Not recovered:')",
                timeout=300000)
        except Exception:
            fail(moments, "verification result never appeared")
        if "Not recovered:" in page.locator("#repair-out").inner_text():
            fail(moments, "repair reported Not recovered")
        page.evaluate("document.getElementById('repair-out')"
                      ".scrollIntoView({block: 'center'})")
        verify = page.locator("#repair-out .verify-ok")
        sel = "#repair-out .verify-ok" if verify.count() else "#repair-out"
        park_beside_box(page, page.locator(sel).bounding_box(timeout=30000))
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
    skip_terminal = "--skip-terminal" in sys.argv[1:]
    RAW.mkdir(parents=True, exist_ok=True)
    moments = {}
    with sync_playwright() as playwright:
        if skip_terminal:
            print("skipping terminal scene (--skip-terminal)", flush=True)
        else:
            record_terminal(playwright, moments)
        record_page(playwright, moments)
    (RAW / "moments.json").write_text(json.dumps(moments, indent=2))
    print("take complete:", moments)


if __name__ == "__main__":
    main()
