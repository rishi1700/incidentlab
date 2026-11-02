# IncidentLab demo page: design brief

A redesign of `web/index.html` for the hackathon video. The page's job is to show one story at a glance, at 1080p: the complaint, the evidence as it arrives, a diagnosis whose claims point to that evidence, the approval, and the verified recovery.

## Rules

- **Change the presentation only.** Keep the endpoints, the SSE events and their data, and all behaviour. Don't touch `investigate.py`, `repair.py`, `lab/` or `eval/`. `web/server.py` changes only if the page needs a field it doesn't get yet.
- **The page still never knows or shows which fault is active,** and it shows only real data from the server.
- **Make no external requests at runtime.** Vendor the fonts into `web/fonts/` with their licence files. Keep CSS and JS in `index.html`.
- **Keep `tests/test_web.py` passing.** Update only tests that check markup, and keep the test that the server never imports the faults module.
- **Work on the `ui-refresh` branch.**

## Concept: an evidence board

Every claim in the report points to an evidence item, and the page makes that link visible. Evidence and report sit side by side, so clicking a citation highlights the cited value on the left while the claim stays in view on the right. The highlight looks like a marker pen on a printout. It is the page's one bold visual move; everything else stays quiet.

## Tokens

| Token | Hex | Use |
|---|---|---|
| `--paper` | `#F3F5F8` | Page background |
| `--sheet` | `#FFFFFF` | Panels |
| `--ink` | `#16202C` | Text, the primary button |
| `--ink-muted` | `#5A6676` | Secondary text, check calls |
| `--rule` | `#D7DDE5` | Hairlines and borders |
| `--exhibit` | `#2450B8` | Evidence IDs, citation chips, links |
| `--marker` | `#FFE45C` | Highlighted cited values (only here, and the P3 row on the results card) |
| `--ok` | `#17784A` | ok, supported, verified |
| `--caution` | `#9A5B00` | Unsupported citations, insufficient evidence |
| `--fault` | `#B42318` | Failed checks, failed verification |

Use these colours for meaning only, never for decoration. Use no shadows and no gradients: panels are white sheets with a `--rule` border. Radii follow hierarchy: panels 12 px, buttons 8 px, tags and chips 6 px.

## Type

- **Atkinson Hyperlegible Next** for all UI text, and **Atkinson Hyperlegible Mono** for code and data: check calls, cause and repair keys, raw evidence, hashes. Both were designed so that similar characters (0 and O, 1, l and I) stay distinct, which matters for IDs and hashes in a compressed video. Both are under the SIL Open Font License; get the woff2 files from the `@fontsource` npm packages or the google/fonts repository. Fall back to `system-ui` and `ui-monospace`.
- **Scale**, at a 1920×1080 viewport with no browser zoom:

| Element | Size |
|---|---|
| Body | 20 px, line height 1.45 |
| Secondary text | 17 px |
| Monospace data | 17 px, line height 1.5 |
| Panel headings | 24 px semibold |
| Diagnosis heading | 32 px bold |
| App name | 26 px bold |

- **Style:** sentence case everywhere. No all-caps labels, no labels above headings, no middle dots between items, no arrows on buttons.

## Layout

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ IncidentLab                        (1) Investigate  (2) Report  (3) Approve  (4) Verify │
├─────────────────────────────────────┬────────────────────────────────────────────────┤
│ What are users seeing?               │ The app can't write to its upload or            │
│ [Uploads have stopped working…] [Investigate] │ staging directory                     │
│ Investigating: 7 checks so far       │ upload_dir_not_writable                         │
│                                      │ Summary in two or three sentences …             │
│ Evidence                             │                                                 │
│ [E1] app: running, db: running    ●  │ Findings                                        │
│      list_containers()               │ • Upload failed: permission denied [E3 lines ✓] │
│ [E2] 200 ok                       ●  │ Ruled out                                       │
│      app_health()                    │ • Volume full: 9% used         [E4 use_percent ✓]│
│ [E3] 82 lines, 3 older omitted    ●  │ Open questions                                  │
│      container_logs(app, tail=100)   │ • …                                             │
│      ┌ raw data, scrolls inside ┐    │ Ask a follow-up  [_____________________] [Ask]  │
│      │ … ▇▇ highlighted line ▇▇ │    │ Repair                                          │
│      └──────────────────────────┘    │ Restore access to the upload and staging        │
│ …                                    │ directories  restore_upload_dir_access          │
│                                      │ [Approve repair]  Nothing changes until you approve. │
│                                      │ ✓ Verified: test file uploaded and downloaded,  │
│                                      │   SHA-256 hashes match                          │
└─────────────────────────────────────┴────────────────────────────────────────────────┘
```

- **Top bar:** the app name on the left; on the right, the four steps of the session, which really are a sequence, so numbering is right here. Upcoming steps are muted outlines, the current step is `--exhibit`, and finished steps are `--ok` with a check. If the diagnosis has no repair, show steps 3 and 4 as "Not needed".
- **Two panels** fill the height below the top bar and scroll independently: the left at 44% of the width (complaint and evidence), the right at 56% (report, follow-up, repair). Text is left-aligned, with line lengths under 80 characters. Below 1100 px wide, stack the panels.
- **Evidence row:** an `[E3]` tag (`--exhibit` text and border); the plain-language summary in ink; a status dot (`--ok`, or `--fault` with the error text); the check call underneath in muted mono. Clicking the row opens the raw data in a box with a maximum height of 360 px that scrolls on its own, so long logs never fill the panel.
- **Citation chip:** the same tag as the evidence item, then the field name in mono, then a check in `--ok` if supported. Unsupported chips use a dashed `--caution` border and the word "unsupported". Clicking a chip scrolls the left panel to centre the item, opens it, scrolls the raw-data box to centre the match, and marks the cited value with `--marker`. Match the value the way `check_citation` does, so values with quotes or backslashes are found even though the raw data is shown as JSON.
- **Diagnosis:** a plain-language heading, with the cause key underneath in muted mono.
  - `volume_full`: The data volume is full
  - `upload_dir_not_writable`: The app can't write to its upload or staging directory
  - `database_unavailable`: The app can't reach its database
  - `healthy`: Uploads are working
  - `insufficient_evidence`: There isn't enough evidence to name a cause
- **Repair:** a plain-language name, with the repair key in mono.
  - `clear_stale_temp_files`: Delete stale partial uploads older than an hour
  - `restore_upload_dir_access`: Restore access to the upload and staging directories
  - `restore_database`: Start or reconnect the database

  "Approve repair" is the only filled button on the page (`--ink` background, white text). Show the results as "Repair applied: …", then "Verified: test file uploaded and downloaded, SHA-256 hashes match" in `--ok`, or "Not recovered: …" in `--fault`.
- **Follow-up:** the question in bold, then the answer, then its citation chips, which behave like the report's.

## Copy

- Drop the "Inject a fault from the terminal first" line: the page is the product, and lab instructions belong in the README.
- **Empty states:** "Evidence appears here as checks run." and "The report appears when the investigation finishes."
- **Status line:** "Investigating: 7 checks so far", then "Finished: 11 checks in 35 s, about $0.07".
- **Errors** say what happened and what to do, for example "Investigation stopped: the model API returned 401. Check MODEL_API_KEY and restart the server."

## Motion

Use motion only where it shows what changed. New evidence rows appear in place (150 ms fade). A cited value gets one marker pulse when its chip is clicked. A step turns green when it finishes. Nothing else animates. Respect `prefers-reduced-motion`.

## Quality

- Keyboard focus is visible on every control.
- Text meets WCAG AA contrast.
- No layout jumps while evidence streams in.

## Video

- Restyle the title, results and closing cards and the caption strip with the same tokens: paper background, ink type, Atkinson Hyperlegible Next. On the results card, mark the P3 row with `--marker`.
- Update `video/record.py` for the new layout. Keep the scenes, timings and narration in `video/SCRIPT.md` unchanged.

## Review before recording

Save 1920×1080 screenshots of six states to `web/screenshots/` (git-ignored): idle, investigating, report, a citation highlighted, a follow-up answered, and the repair verified. A fake client is fine for these design screenshots only. The video must still be a real take.
