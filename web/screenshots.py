#!/usr/bin/env python3
"""Capture the six design-review screenshots with synthetic P3-like state.

Drives the page's own functions (addCheck, showReport, renderAnswer,
renderRepair) with fabricated events: no lab, no model. Re-runnable.

Usage: PLAYWRIGHT_BROWSERS_PATH=video/.browsers \\
       video/.venv/bin/python web/screenshots.py
"""
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "screenshots"

from playwright.sync_api import sync_playwright

PERM_LINE = ('2026-11-02T16:36:44Z PermissionError: [Errno 13] Permission denied: '
             '"/data/tmp/abc123.part"')

CHECKS = [
    {"evidence_id": "E1", "check": "app_health", "arguments": "{}",
     "ok": True, "data": {"status_code": 200, "body": "ok"}, "summary": "200 ok"},
    {"evidence_id": "E2", "check": "container_logs",
     "arguments": '{"name": "incidentlab-app-1", "tail": 100}',
     "ok": True,
     "data": {"lines": ["2026-11-02T16:35:01Z POST /upload 500",
                        PERM_LINE,
                        "2026-11-02T16:35:02Z POST /upload 500"],
              "older_lines_omitted": 11},
     "summary": "3 lines, 11 older omitted"},
    {"evidence_id": "E3", "check": "disk_usage", "arguments": "{}",
     "ok": True,
     "data": {"use_percent": 9.0, "available_mb": 410.2, "inode_use_percent": 3.0},
     "summary": "9% used, 410 MB free"},
    {"evidence_id": "E4", "check": "path_access",
     "arguments": '{"path": "/srv/lab-data/uploads"}',
     "ok": True,
     "data": {"path": "/srv/lab-data/uploads", "mode": "0755",
              "owner_uid": 1000, "owner_gid": 1000, "app_user_can_write": True},
     "summary": "uploads: app user can write (0755 1000:1000)"},
]

DONE = {
    "report": {
        "cause": "upload_dir_not_writable",
        "summary": ("Uploads fail with 500: the app cannot write staging files to /data/tmp. "
                    "The database is healthy and the volume has free space."),
        "findings": [
            {"claim": "Uploads fail with permission denied writing the staging file",
             "citation": {"evidence_id": "E2", "field": "lines", "value": PERM_LINE}},
            {"claim": "The upload directory itself is writable",
             "citation": {"evidence_id": "E4", "field": "app_user_can_write", "value": "true"}},
        ],
        "ruled_out": [
            {"cause": "volume_full", "reason": "Only 9% of the data volume is used",
             "citation": {"evidence_id": "E3", "field": "use_percent", "value": "9.0"}},
            {"cause": "database_unavailable", "reason": "The database answers and reports healthy",
             "citation": {"evidence_id": "E9", "field": "health", "value": "healthy"}},
        ],
        "open_questions": ["Who changed the staging directory, and when?"],
    },
    "validation": {
        "citations": [
            {"kind": "finding", "text": "Uploads fail with permission denied writing the staging file",
             "supported": True, "detail": "E2.lines matches",
             "citation": {"evidence_id": "E2", "field": "lines", "value": PERM_LINE}},
            {"kind": "finding", "text": "The upload directory itself is writable",
             "supported": True, "detail": "E4.app_user_can_write matches",
             "citation": {"evidence_id": "E4", "field": "app_user_can_write", "value": "true"}},
            {"kind": "ruled_out", "text": "volume_full: Only 9% of the data volume is used",
             "supported": True, "detail": "E3.use_percent matches",
             "citation": {"evidence_id": "E3", "field": "use_percent", "value": "9.0"}},
            {"kind": "ruled_out", "text": "database_unavailable: The database answers and reports healthy",
             "supported": False, "detail": "E9 is not in the evidence trail",
             "citation": {"evidence_id": "E9", "field": "health", "value": "healthy"}},
        ],
        "supported": 3, "total": 4, "warnings": [],
    },
    "repair": "restore_upload_dir_access",
    "seconds": 35.2, "cost_usd": 0.069,
}

ANSWER = {
    "text": ("Full disk is ruled out: /srv/lab-data was 9% used and uploads fail with "
             "Permission denied rather than no space."),
    "citations": [
        {"citation": {"evidence_id": "E3", "field": "use_percent", "value": "9.0"},
         "supported": True, "detail": "E3.use_percent matches"},
        {"citation": {"evidence_id": "E2", "field": "lines", "value": PERM_LINE},
         "supported": True, "detail": "E2.lines matches"},
    ],
    "seconds": 10.6, "cost_usd": 0.102,
}

REPAIR = {
    "repair": "restore_upload_dir_access",
    "repair_result": {"ok": True, "applied": True,
                      "detail": "uploads/ and tmp/ owner 1000:1000 mode 0755"},
    "verification": {"ok": True,
                     "detail": "test file uploaded and downloaded, SHA-256 hashes match"},
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto((ROOT / "web" / "index.html").as_uri())
        page.wait_for_selector("#go")
        page.screenshot(path=str(OUT / "1-idle.png"))

        for ev in CHECKS[:3]:
            page.evaluate("addCheck", ev)
        page.evaluate("setStep(1, 'current')")
        time.sleep(0.4)
        page.screenshot(path=str(OUT / "2-investigating.png"))

        page.evaluate("addCheck", CHECKS[3])
        page.evaluate("([done]) => { showReport(done); setStep(1, 'done'); "
                      "setStep(2, 'done'); setStep(3, 'current'); "
                      "document.getElementById('status').textContent = "
                      "finishedStatus(4, done.seconds, done.cost_usd); "
                      "document.getElementById('report-sec').scrollIntoView({block: 'start'}); }",
                      [DONE])
        time.sleep(0.4)
        page.screenshot(path=str(OUT / "3-report.png"))

        page.locator("#findings .cite-link").first.click()
        time.sleep(0.6)
        page.screenshot(path=str(OUT / "4-citation.png"))

        page.evaluate("([q, ev]) => { const box = startFollowup(q); renderAnswer(box, ev); "
                      "document.getElementById('follow-sec').scrollIntoView({block: 'start'}); }",
                      ["What rules out a full disk?", ANSWER])
        time.sleep(0.4)
        page.screenshot(path=str(OUT / "5-followup.png"))

        page.evaluate("([r]) => { renderRepair(r); setStep(3, 'done'); setStep(4, 'done'); "
                      "document.getElementById('repair-out').scrollIntoView({block: 'center'}); }",
                      [REPAIR])
        time.sleep(0.4)
        page.screenshot(path=str(OUT / "6-repair.png"))
        browser.close()
    print("screenshots in", OUT)


if __name__ == "__main__":
    main()
