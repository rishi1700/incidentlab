#!/usr/bin/env python3
"""Assemble the demo video from one take's raw footage and the cards.

Reads raw/{terminal,page}.webm + moments.json and cards/*.jpg, writes
stills/*, scenes/*.mp4 and incidentlab-demo.mp4, then verifies every scene
duration with ffprobe. Speeds footage only with an on-screen speed label,
pads with held last frames, and ends each scene with ~1s of stillness.

Paths default under video/out/; VIDEO_RAW and VIDEO_OUT override them
(used to validate this script against synthetic clips in /tmp).

Run: video/.venv/bin/python video/assemble.py  (via video/build.sh)
"""
import json
import math
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = Path(os.environ.get("VIDEO_RAW", ROOT / "video" / "out" / "raw"))
OUT = Path(os.environ.get("VIDEO_OUT", ROOT / "video" / "out"))
CARDS = OUT / "cards"

FPS = 30
SCENES = {1: 14, 2: 22, 3: 24, 4: 28, 5: 20, 6: 23, 7: 29, 8: 17}
ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
       "-pix_fmt", "yuv420p", "-r", "30", "-an"]
BASE_VF = ("scale=1920:1080:force_original_aspect_ratio=decrease,"
           "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def run(cmd):
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True)
    if proc.returncode != 0:
        print("$ " + " ".join(cmd), flush=True)
        print(proc.stderr[-3000:], flush=True)
        sys.exit(f"ffmpeg failed (exit {proc.returncode})")


def finish(tmp_out, final):
    Path(tmp_out).rename(final)


def drawtext(text, where, enable=None, size=44):
    esc = text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    if where == "bottom":
        pos = "x=(w-text_w)/2:y=h-150"
    else:
        pos = "x=w-text_w-50:y=50"
    spec = (f"drawtext=fontfile={FONT}:fontsize={size}:fontcolor=white:"
            f"text='{esc}':box=1:boxcolor=black@0.65:boxborderw=24:{pos}")
    if enable is not None:
        spec += f":enable={enable}"
    return spec


def between(a, b):
    return f"between(t\\,{a:.2f}\\,{b:.2f})"


def after(t):
    return f"gte(t\\,{t:.2f})"


def scene_from_segments(src, segments, duration, overlays, out):
    """segments: [(start, end, speed)]. overlays: [filter strings]."""
    cmd = ["ffmpeg", "-y"]
    for start, end, _ in segments:
        cmd += ["-ss", f"{start:.2f}", "-i", str(src)]
    parts, labels = [], []
    for i, (start, end, speed) in enumerate(segments):
        length = max(end - start, 0.1)
        parts.append(f"[{i}:v]trim=start=0:end={length:.2f},"
                     f"setpts=(PTS-STARTPTS)/{speed:g}[s{i}]")
        labels.append(f"[s{i}]")
    vf = ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(segments)}:v=1:a=0[v]"
    vf += f";[v]{BASE_VF}"
    for overlay in overlays:
        vf += f",{overlay}"
    frames = duration * FPS
    vf += (f",tpad=stop_mode=clone:stop_duration={duration},"
           f"trim=end_frame={frames},setpts=PTS-STARTPTS")
    tmp = str(out) + ".tmp.mp4"
    run(cmd + ["-filter_complex", vf, "-frames:v", str(frames), *ENC, tmp])
    finish(tmp, out)


def still_scene(jpg, duration, out):
    frames = duration * FPS
    tmp = str(out) + ".tmp.mp4"
    run(["ffmpeg", "-y", "-framerate", str(FPS), "-loop", "1", "-i", str(jpg),
         "-vf", f"{BASE_VF},trim=end_frame={frames},setpts=PTS-STARTPTS",
         "-frames:v", str(frames), *ENC, tmp])
    finish(tmp, out)


def speed_of(raw, budget):
    if raw <= budget:
        return 1.0
    return math.ceil(raw / budget * 10) / 10  # round up: the tail only pads, never cuts


def speed_label(speed):
    return f"{speed:g}\u00d7 speed"


def main():
    moments = json.loads((RAW / "moments.json").read_text())
    if "terminal" not in moments or "page" not in moments:
        sys.exit("moments.json is from an incomplete take; run video/record.sh first")
    for name in ("terminal.webm", "page.webm"):
        if not (RAW / name).exists():
            sys.exit(f"{RAW / name} missing; run video/record.sh first")
    term, page = RAW / "terminal.webm", RAW / "page.webm"
    tm, pm = moments["terminal"], moments["page"]
    scenes = OUT / "scenes"
    scenes.mkdir(parents=True, exist_ok=True)
    stills = OUT / "stills"
    stills.mkdir(parents=True, exist_ok=True)

    # Stills first, before the render.
    for name, at in (("report", pm["report"] + 1.0),
                     ("followup", pm["answer"] + 1.0),
                     ("verify", pm["verify"] + 1.0)):
        run(["ffmpeg", "-y", "-ss", f"{at:.2f}", "-i", str(page),
             "-frames:v", "1", "-q:v", "2", str(stills / f"{name}.jpg")])
    run(["cp", str(CARDS / "results.jpg"), str(stills / "results.jpg")])
    print("stills in", stills)

    plan = []  # (scene, duration, note)

    still_scene(CARDS / "title.jpg", SCENES[1], scenes / "1.mp4")
    plan.append((1, SCENES[1], "title card"))

    # Scene 2: terminal, 22 s. Caption over the last 5 s.
    raw = tm["end"] - 0.4
    speed = speed_of(raw, 21.0)
    overlays = [drawtext("Uploads fail, but the health check says 200", "bottom",
                         enable=after(17.0))]
    if speed > 1.0:
        overlays.append(drawtext(speed_label(speed), "top-right", enable=between(0, 21.0)))
    scene_from_segments(term, [(0.4, tm["end"], speed)], SCENES[2], overlays,
                        scenes / "2.mp4")
    plan.append((2, SCENES[2], "terminal" + ("" if speed == 1.0 else f" {speed_label(speed)}")))

    # Scene 3: investigate, 24 s. First 1.2 s at 1x (the click), then speed.
    click, report = pm["investigate_click"], pm["report"]
    speed = speed_of(report - click - 1.2, 23.0 - 1.2)
    overlays = []
    if speed > 1.0:
        overlays.append(drawtext(speed_label(speed), "top-right",
                                 enable=between(1.2, 23.0)))
    scene_from_segments(page, [(click, click + 1.2, 1.0), (click + 1.2, report, speed)],
                        SCENES[3], overlays, scenes / "3.mp4")
    plan.append((3, SCENES[3], f"investigate ({speed_label(speed)} wait)" if speed > 1.0
                else "investigate 1x"))

    # Scene 4: report, 28 s. Ground-truth caption throughout.
    raw = pm["questions"] - pm["report"]
    speed = speed_of(raw, 27.0)
    overlays = [drawtext("Injected fault, hidden from the model: "
                         "staging directory not writable", "bottom")]
    if speed > 1.0:
        overlays.append(drawtext(speed_label(speed), "top-right", enable=between(0, 27.0)))
    scene_from_segments(page, [(pm["report"], pm["questions"], speed)],
                        SCENES[4], overlays, scenes / "4.mp4")
    plan.append((4, SCENES[4], "report" + (f" {speed_label(speed)}" if speed > 1.0 else "")))

    # Scene 5: follow-up, 20 s. Typing 1x, wait sped, 3 s tail + holds.
    q, ask, answer = pm["questions"], pm["ask"], pm["answer"]
    typing = ask - q
    wait_out = 20.0 - 1.0 - typing - 3.0
    if wait_out < 3.0:  # very slow typing: shrink the tail, keep 1 s stillness
        wait_out = 3.0
    speed = speed_of(answer - ask, wait_out)
    tail_end = answer + 3.0
    overlays = []
    if speed > 1.0:
        overlays.append(drawtext(speed_label(speed), "top-right",
                                 enable=between(typing, typing + wait_out)))
    scene_from_segments(page, [(q, ask, 1.0), (ask, answer, speed), (answer, tail_end, 1.0)],
                        SCENES[5], overlays, scenes / "5.mp4")
    plan.append((5, SCENES[5], f"follow-up ({speed_label(speed)} wait)" if speed > 1.0
                else "follow-up 1x"))

    # Scene 6: repair, 23 s.
    raw = pm["verify"] - pm["answer"]
    speed = speed_of(raw, 22.0)
    overlays = []
    if speed > 1.0:
        overlays.append(drawtext(speed_label(speed), "top-right", enable=between(0, 22.0)))
    scene_from_segments(page, [(pm["answer"], pm["verify"], speed)],
                        SCENES[6], overlays, scenes / "6.mp4")
    plan.append((6, SCENES[6], "repair" + (f" {speed_label(speed)}" if speed > 1.0 else "")))

    still_scene(CARDS / "results.jpg", SCENES[7], scenes / "7.mp4")
    plan.append((7, SCENES[7], "results card"))
    still_scene(CARDS / "close.jpg", SCENES[8], scenes / "8.mp4")
    plan.append((8, SCENES[8], "close card"))

    # Join with identical codec settings.
    lst = OUT / "scenes" / "join.txt"
    lst.write_text("".join(f"file '{scenes / f'{i}.mp4'}'\n" for i in range(1, 9)))
    final = OUT / "incidentlab-demo.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
         "-c", "copy", str(final) + ".tmp.mp4"])
    finish(str(final) + ".tmp.mp4", final)

    # Verify exact durations.
    print(f"{'scene':>5} {'target':>6} {'actual':>8}  note")
    ok = True
    total = 0.0
    for scene, target, note in plan:
        actual = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(scenes / f"{scene}.mp4")],
            stdout=subprocess.PIPE, text=True, check=True).stdout)
        total += actual
        good = abs(actual - target) < 0.06
        ok = ok and good
        print(f"{scene:>5} {target:>6} {actual:>8.2f}  {note}"
              + ("" if good else "  MISMATCH"))
    print(f"total {total:.2f}s (want 177.00s)")
    if not ok or abs(total - 177.0) > 0.2:
        sys.exit("duration check FAILED")
    print("duration check passed:", final)


if __name__ == "__main__":
    main()
