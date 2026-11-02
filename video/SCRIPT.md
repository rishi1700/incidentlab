# IncidentLab demo video: script and build plan

A silent 1920×1080 video, 2:57 long, built from one real run in the lab. Rishi adds the narration afterwards as a voiceover, so each scene must last exactly as long as listed below.

## Rules

- **Real footage only.** Scenes 2–6 come from one real run against Meta Model API on the real lab, recorded in a single session. No fake client, and no edited, staged or hand-written outputs. Overlays are fine: captions, speed labels, and a pointer that shows where clicks happen.
- **Never show or print the API key.** It's in `MODEL_API_KEY`; use it only through `web/server.py` and the lab scripts.
- **Label sped-up footage** with its speed, for example "2× speed". Captions and speed labels live in a strip below the footage, with the footage scaled down to make room, so they never cover content.
- **The results card's numbers come from `eval/results/full-1`** (`runs.jsonl` or `summary.md`), computed by code, never typed by hand.
- **Show which fault was injected only after the diagnosis appears,** as ground truth that was hidden from the model.
- **Make text readable** at 1080p on a laptop: zoom the page to about 150% and use a large terminal font.

## Output

- `video/out/incidentlab-demo.mp4`: H.264, yuv420p, 1920×1080, 30 fps, no audio. Each scene lasts exactly the time listed.
- `video/record.sh` records the footage and `video/build.sh` renders the cards and assembles the video. Both can be re-run.
- `video/out/` is git-ignored.

## How to build it

- **Before recording:** reset the lab, recreate the containers so the logs are clean, wait for `/health` to return 200, and confirm that `lab/verify.py` passes. After recording, reset the lab.
- **Terminal scene:** record a real shell, ideally `ttyd` driven by Playwright; asciinema with agg is fine too. Not a mock terminal.
- **Page scenes:** Playwright with Chromium, recording video or JPEG screenshots at quality 92. Show a visible pointer, and type at a human pace.
- **Cards:** HTML rendered at 1920×1080 to JPEG at quality 92, held as a still with `-framerate 30 -i card.jpg -vf format=yuv420p,loop=-1:1,trim=duration=N -pix_fmt yuv420p`.
- **Fitting:** fit live footage to its scene by speeding it up, with a label, or by holding the last frame. Leave about a second of stillness at the end of each scene.
- **Encoding:** encode one file per scene, all with the same size, frame rate and encoder settings (`libx264 -preset veryfast -crf 20`). Write each to a temporary name ending in `.mp4` and rename it when ffmpeg succeeds. Join the scenes with the concat demuxer and `-c copy`.
- **Check first:** before the full render, save full-size stills of the busiest moments to `video/out/stills/`: the report, the follow-up answer, the verification result and the results card, plus one frame per click (Investigate, citation, Ask, Approve repair). Check them for readability.

## Scenes

### 1. Title card: 0:00–0:14 (14 s)

**On screen:**
- IncidentLab
- Finds why a Docker app stopped working, cites the evidence, and verifies the fix
- Muse Spark on Meta Model API

**Narration:** When a self-hosted app breaks, the obvious signals can lie. IncidentLab uses Muse Spark to investigate, cites the evidence behind every claim, and checks that the fix really worked.

### 2. Break the lab: 0:14–0:36 (22 s)

**On screen:** a terminal in the repo folder, running:

```
python3 lab/verify.py
python3 lab/faults.py inject P3
python3 lab/verify.py
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8080/health
```

The first upload succeeds, the second fails with HTTP 500, and the health check prints 200. At the end, show the caption "Uploads fail, but the health check says 200".

**Narration:** This is a small upload app with a Postgres database, and uploads work. Now I inject a fault. Uploads fail with a server error, but the health check still returns 200.

### 3. Investigate: 0:36–1:00 (24 s)

**On screen:** the demo page, with the complaint already in the box. Click Investigate. The evidence trail fills in as the checks run, sped up and labelled.

**Narration:** I give IncidentLab the same complaint a user would. Muse Spark decides which read-only checks to run: containers, logs, disk, permissions and Docker events, several at a time. Each result gets an evidence ID, a one-line summary and the raw data.

### 4. The report: 1:00–1:28 (28 s)

**On screen:** the report, with the diagnosis, the findings and the ruled-out causes, each with its supported mark. Click the first finding's citation: the page jumps to that evidence and highlights the value. Then show the open questions. Once the diagnosis is visible, show the caption "Injected fault, hidden from the model: staging directory not writable".

**Narration:** The report names the cause: the app can't write to its staging directory, even though the upload folder and the health check look fine. Every finding cites its evidence, and code checks each citation against the raw data. Click one, and it jumps to the exact value.

### 5. Follow-up: 1:28–1:48 (20 s)

**On screen:** type "What rules out a full disk?" in the follow-up box and click Ask. The answer appears with its citations marked. Speed up the wait and label it.

**Narration:** I can challenge it: what rules out a full disk? It answers from the evidence it already collected, and those citations are checked the same way.

### 6. Repair and verify: 1:48–2:11 (23 s)

**On screen:** the mapped repair. Click Approve repair. Show the repair result, then "Verification: OK: upload/download SHA-256 hashes match".

**Narration:** Each diagnosis maps to one predefined repair, and nothing changes until I approve it. Then IncidentLab checks recovery the way a user would: it uploads a random file, downloads it and compares SHA-256 hashes. A running container isn't proof. A successful round trip is.

### 7. Results: 2:11–2:40 (29 s)

**On screen:** a results card, with every number computed from `eval/results/full-1`:
- Evaluation: 15 cases, 3 runs each, expected answers fixed in advance
- IncidentLab: 44 of 45 correct. Rule-based baseline: 42 of 45
- This case (P3): IncidentLab 3 of 3. Baseline 0 of 3, called it healthy
- False alarms: 0. Citations matching the evidence: 99%. Repairs verified: 30 of 30

**Narration:** I tested it on 15 lab cases, three runs each, with the expected answers fixed in advance. IncidentLab was right in 44 of 45 runs, with no false alarms. A rule-based baseline using the same checks got 42: it called this exact case healthy every time. 99% of citations matched the evidence, and every repair that ran restored uploads.

### 8. Close: 2:40–2:57 (17 s)

**On screen:**
- github.com/rishi1700/incidentlab
- Muse Spark on Meta Model API, built with Muse Code
- About 5 cents per investigation

**Narration:** IncidentLab runs on Muse Spark's tool calling, structured output and reasoning, and much of it was built with Muse Code. Each investigation costs about five cents. The code, the evaluation and every result are on GitHub.
