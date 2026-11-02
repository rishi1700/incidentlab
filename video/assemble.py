#!/usr/bin/env python3
"""Assemble the demo video from one take's raw footage and the cards.

Reads raw/{terminal,page}.webm + moments.json and cards/*.jpg, writes
stills/*, scenes/*.mp4 and incidentlab-demo.mp4, then verifies every scene
duration with ffprobe. Speeds footage only with an on-screen speed label,
pads with held last frames, and ends each scene with ~1s of stillness.

Paths default under video/out/; VIDEO_RAW and VIDEO_OUT override them
(used to validate this script against synthetic clips in /tmp).
TERMINAL_CLIP points scene 2 at the user's own screen recording
instead of raw/terminal.webm (from build.sh --terminal-clip).

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
# out_range=limited: JPEG cards decode as full-range yuvj420p, and the format
# filter retags without rescaling, which leaked full-range frames (shown as
# yuvj420p) into the H.264 output. Footage is already limited, so this is a
# no-op for scenes 2-6.
# Live scenes (2-6) shrink the footage to make room for a bottom strip that
# carries captions and speed labels, so they never cover content. Cards stay
# full-frame: they have no overlays.
STRIP_H = 150
FOOT_H = 1080 - STRIP_H
STRIP_BG = "0xF3F5F8"
FONT = str(ROOT / "web" / "fonts" / "atkinson-next-700.woff2")
CARD_VF = ("scale=1920:1080:force_original_aspect_ratio=decrease:out_range=limited,"
           "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p")


def run(cmd):
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True)
    if proc.returncode != 0:
        print("$ " + " ".join(cmd), flush=True)
        print(proc.stderr[-3000:], flush=True)
        sys.exit(f"ffmpeg failed (exit {proc.returncode})")


def finish(tmp_out, final):
    Path(tmp_out).rename(final)


def _esc(text):
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def caption(text, enable=None, size=42):
    """Centered ink caption on the strip's bottom line."""
    spec = (f"drawtext=fontfile={FONT}:fontsize={size}:fontcolor=0x16202C:"
            f"text='{_esc(text)}':"
            f"x=(w-text_w)/2:y=h-text_h-14")
    if enable is not None:
        spec += f":enable={enable}"
    return spec


def speed_tag(text, enable, size=38):
    """Speed label on the strip's top line, right side."""
    spec = (f"drawtext=fontfile={FONT}:fontsize={size}:fontcolor=0x2450B8:"
            f"text='{_esc(text)}':"
            f"x=w-text_w-40:y=14:enable={enable}")
    return spec


def between(a, b):
    return f"between(t\\,{a:.2f}\\,{b:.2f})"


def after(t):
    return f"gte(t\\,{t:.2f})"


def probe_duration(path):
    return float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        stdout=subprocess.PIPE, text=True, check=True).stdout)


def clamp_cut(t, dur):
    """Clamp a seek/cut timestamp into a real clip. trim() tolerates an end
    past EOF, but a start past EOF yields no frames, so keep starts inside."""
    return max(0.0, min(t, dur - 0.05))


def scene_from_segments(src, segments, duration, overlays, out, clip_dur):
    """segments: [(start, end, speed)]. overlays render into the bottom
    strip; the footage above is pillarboxed, never covered."""
    clamped = []
    for start, end, speed in segments:
        start = clamp_cut(start, clip_dur)
        end = max(start + 0.05, min(end, clip_dur))
        clamped.append((start, end, speed))
    segments = clamped
    cmd = ["ffmpeg", "-y"]
    for start, end, _ in segments:
        cmd += ["-ss", f"{start:.2f}", "-i", str(src)]
    strip_in = len(segments)
    cmd += ["-f", "lavfi", "-i",
            f"color=c={STRIP_BG}:s=1920x{STRIP_H}:r={FPS}:d={duration}"]
    parts, labels = [], []
    for i, (start, end, speed) in enumerate(segments):
        length = max(end - start, 0.1)
        parts.append(f"[{i}:v]trim=start=0:end={length:.2f},"
                     f"setpts=(PTS-STARTPTS)/{speed:g}[s{i}]")
        labels.append(f"[s{i}]")
    vf = ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(segments)}:v=1:a=0[v]"
    vf += (f";[v]scale=1920:{FOOT_H}:force_original_aspect_ratio=decrease:"
           f"out_range=limited,pad=1920:{FOOT_H}:(ow-iw)/2:(oh-ih)/2:color={STRIP_BG},"
           f"setsar=1,fps={FPS},format=yuv420p[foot]")
    strip = f"[{strip_in}:v]format=yuv420p"
    for overlay in overlays:
        strip += f",{overlay}"
    vf += ";" + strip + "[strip];[foot][strip]vstack=inputs=2[vout]"
    frames = duration * FPS
    vf += (f";[vout]tpad=stop_mode=clone:stop_duration={duration},"
           f"trim=end_frame={frames},setpts=PTS-STARTPTS")
    tmp = str(out) + ".tmp.mp4"
    run(cmd + ["-filter_complex", vf, "-frames:v", str(frames), *ENC, tmp])
    finish(tmp, out)


def still_scene(jpg, duration, out):
    frames = duration * FPS
    tmp = str(out) + ".tmp.mp4"
    run(["ffmpeg", "-y", "-framerate", str(FPS), "-loop", "1", "-i", str(jpg),
         "-vf", f"{CARD_VF},trim=end_frame={frames},setpts=PTS-STARTPTS",
         "-frames:v", str(frames), *ENC, tmp])
    finish(tmp, out)


def speed_of(raw, budget):
    if raw <= budget:
        return 1.0
    return math.ceil(raw / budget * 10) / 10  # round up: the tail only pads, never cuts


def speed_label(speed):
    return f"{speed:g}\u00d7 speed"


def main():
    term_clip = os.environ.get("TERMINAL_CLIP") or None
    moments = json.loads((RAW / "moments.json").read_text())
    if "page" not in moments or ("terminal" not in moments and not term_clip):
        sys.exit("moments.json is from an incomplete take; run video/record.sh first")
    if term_clip and not Path(term_clip).is_file():
        sys.exit(f"terminal clip not found: {term_clip}")
    for name in (["page.webm"] if term_clip else ["terminal.webm", "page.webm"]):
        if not (RAW / name).exists():
            sys.exit(f"{RAW / name} missing; run video/record.sh first")
    page = RAW / "page.webm"
    pm = moments["page"]
    page_dur = probe_duration(page)
    if term_clip:
        print(f"raw footage: terminal clip {term_clip}, page {page_dur:.2f}s")
    else:
        term = RAW / "terminal.webm"
        tm = moments["terminal"]
        term_dur = probe_duration(term)
        print(f"raw footage: terminal {term_dur:.2f}s, page {page_dur:.2f}s")
    scenes = OUT / "scenes"
    scenes.mkdir(parents=True, exist_ok=True)
    stills = OUT / "stills"
    stills.mkdir(parents=True, exist_ok=True)

    # Stills first, before the render. Clamp into the clip so a seek past
    # EOF can never starve the encoder, and write full range for mjpeg.
    for name, at in (("report", pm["report"] + 1.0),
                     ("followup", pm["answer"] + 1.0),
                     ("verify", pm["verify"] + 1.0)):
        want = at
        at = clamp_cut(at, page_dur)
        if abs(at - want) > 0.01:
            print(f"still {name}: clamped {want:.2f}s to {at:.2f}s (clip is {page_dur:.2f}s)")
        run(["ffmpeg", "-y", "-ss", f"{at:.2f}", "-i", str(page),
             "-frames:v", "1", "-vf", "scale=out_range=full", "-q:v", "2",
             str(stills / f"{name}.jpg")])
    run(["cp", str(CARDS / "results.jpg"), str(stills / "results.jpg")])
    for src in sorted(RAW.glob("click-*.png")):
        run(["ffmpeg", "-y", "-i", str(src),
             "-vf", "scale=out_range=full", "-q:v", "2",
             str(stills / f"{src.stem}.jpg")])
    print("stills in", stills)

    plan = []  # (scene, duration, note)

    still_scene(CARDS / "title.jpg", SCENES[1], scenes / "1.mp4")
    plan.append((1, SCENES[1], "title card"))

    # Scene 2: terminal, 22 s. Caption over the last 5 s.
    if term_clip:
        # The user's own screen recording (.mov, any size): the whole
        # clip, sped to fit with a label, never cut. The shared scaler
        # fits it above the strip without cropping.
        clip_dur = probe_duration(term_clip)
        speed = speed_of(clip_dur, 21.0)
        overlays = [caption("Uploads fail, but the health check says 200",
                              enable=after(17.0))]
        if speed > 1.0:
            overlays.append(speed_tag(speed_label(speed), between(0, 21.0)))
        scene_from_segments(term_clip, [(0, clip_dur, speed)], SCENES[2],
                            overlays, scenes / "2.mp4", clip_dur)
        plan.append((2, SCENES[2], "terminal clip"
                     + ("" if speed == 1.0 else f" {speed_label(speed)}")))
    else:
        raw = tm["end"] - 0.4
        speed = speed_of(raw, 21.0)
        overlays = [caption("Uploads fail, but the health check says 200",
                              enable=after(17.0))]
        if speed > 1.0:
            overlays.append(speed_tag(speed_label(speed), between(0, 21.0)))
        scene_from_segments(term, [(0.4, tm["end"], speed)], SCENES[2], overlays,
                            scenes / "2.mp4", term_dur)
        plan.append((2, SCENES[2], "terminal"
                     + ("" if speed == 1.0 else f" {speed_label(speed)}")))

    # Scene 3: investigate, 24 s. First 1.2 s at 1x (the click), then speed.
    click, report = pm["investigate_click"], pm["report"]
    speed = speed_of(report - click - 1.2, 23.0 - 1.2)
    overlays = []
    if speed > 1.0:
        overlays.append(speed_tag(speed_label(speed), between(1.2, 23.0)))
    scene_from_segments(page, [(click, click + 1.2, 1.0), (click + 1.2, report, speed)],
                        SCENES[3], overlays, scenes / "3.mp4", page_dur)
    plan.append((3, SCENES[3], f"investigate ({speed_label(speed)} wait)" if speed > 1.0
                else "investigate 1x"))

    # Scene 4: report, 28 s. Ground-truth caption throughout.
    raw = pm["questions"] - pm["report"]
    speed = speed_of(raw, 27.0)
    overlays = [caption("Injected fault, hidden from the model: "
                          "staging directory not writable")]
    if speed > 1.0:
        overlays.append(speed_tag(speed_label(speed), between(0, 27.0)))
    scene_from_segments(page, [(pm["report"], pm["questions"], speed)],
                        SCENES[4], overlays, scenes / "4.mp4", page_dur)
    plan.append((4, SCENES[4], "report" + (f" {speed_label(speed)}" if speed > 1.0 else "")))

    # Scene 5: follow-up, 20 s. Typing 1x, only the wait sped, then the
    # answer held on screen for the rest of the scene.
    q, ask, answer, approve = pm["questions"], pm["ask"], pm["answer"], pm["approve"]
    typing = ask - q
    wait_out = max(3.0, 20.0 - typing - 8.0)
    speed = speed_of(answer - ask, wait_out)
    tail_end = min(answer + 6.0, max(answer + 1.0, approve - 0.3))
    overlays = []
    if speed > 1.0:
        overlays.append(speed_tag(speed_label(speed), between(typing, typing + wait_out)))
    scene_from_segments(page, [(q, ask, 1.0), (ask, answer, speed), (answer, tail_end, 1.0)],
                        SCENES[5], overlays, scenes / "5.mp4", page_dur)
    plan.append((5, SCENES[5], f"follow-up ({speed_label(speed)} wait)" if speed > 1.0
                else "follow-up 1x"))

    # Scene 6: repair, 23 s.
    raw = pm["verify"] - pm["answer"]
    speed = speed_of(raw, 22.0)
    overlays = []
    if speed > 1.0:
        overlays.append(speed_tag(speed_label(speed), between(0, 22.0)))
    scene_from_segments(page, [(pm["answer"], pm["verify"], speed)],
                        SCENES[6], overlays, scenes / "6.mp4", page_dur)
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

    # Verify exact durations and the delivery spec on every scene.
    print(f"{'scene':>5} {'target':>6} {'actual':>8}  note")
    ok = True
    total = 0.0
    for scene, target, note in plan:
        path = scenes / f"{scene}.mp4"
        actual = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            stdout=subprocess.PIPE, text=True, check=True).stdout)
        spec = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_name,width,height,avg_frame_rate,pix_fmt",
             "-of", "default=noprint_wrappers=1", str(path)],
            stdout=subprocess.PIPE, text=True, check=True).stdout
        good_spec = all(line in spec for line in
                        ("codec_name=h264", "width=1920", "height=1080",
                         "avg_frame_rate=30/1", "pix_fmt=yuv420p"))
        total += actual
        good = abs(actual - target) < 0.06 and good_spec
        ok = ok and good
        print(f"{scene:>5} {target:>6} {actual:>8.2f}  {note}"
              + ("" if good else "  MISMATCH"))
    print(f"total {total:.2f}s (want 177.00s)")
    if not ok or abs(total - 177.0) > 0.2:
        sys.exit("delivery check FAILED")
    print("delivery check passed:", final)


if __name__ == "__main__":
    main()
