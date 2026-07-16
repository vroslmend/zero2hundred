from __future__ import annotations

import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import threading
from urllib.parse import unquote, urlsplit
import webbrowser

from zero2hundred.errors import MediaError
from zero2hundred.media import Toolchain

DEFAULT_THUMBNAIL_LIMIT = 1200
_CHUNK_SIZE = 64 * 1024
_RANGE_PATTERN = re.compile(r"bytes=(\d*)-(\d*)$")
_BROWSER_SAFE_CODECS = {"h264", "vp8", "vp9", "av1"}
_BROWSER_SAFE_PIXEL_FORMATS = {"yuv420p", "yuvj420p"}


def thumbnail_indices(count: int, limit: int = DEFAULT_THUMBNAIL_LIMIT) -> list[int]:
    """Return the sorted, unique frame indices to thumbnail, always including 0."""
    if count <= 0:
        return []
    step = _step_for(count, limit)
    return list(range(0, count, step))


def _step_for(count: int, limit: int) -> int:
    if count <= limit:
        return 1
    return math.ceil(count / limit)


def extract_thumbnails(
    path: Path, toolchain: Toolchain, step: int, workdir: Path
) -> list[Path]:
    """Extract every `step`-th decoded frame as a scaled JPEG into `workdir/frames/`."""
    frames_dir = workdir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    pattern = frames_dir / "%06d.jpg"

    if step <= 1:
        video_filter = "scale=-2:160"
    else:
        video_filter = rf"select='not(mod(n\,{step}))',scale=-2:160"

    # FFmpeg's image2 muxer defaults to CFR pacing, which drops/duplicates frames on
    # variable-frame-rate input unless told to keep every decoded frame verbatim. "vfr" mode
    # still drops frames whose timestamps round to the same output tick (measured: 445/473 on
    # a real VFR clip with a frame-rate step-up near the end) because it actively dedupes by
    # rounded timestamp; "passthrough" mode forwards every decoded frame unconditionally
    # (measured: 473/473), only warning about non-monotonic muxer timestamps, which is
    # harmless here since files are named by sequential frame index, not by timestamp.
    completed = _run_ffmpeg(toolchain, path, video_filter, ["-fps_mode", "passthrough"], pattern)
    if completed.returncode:
        completed = _run_ffmpeg(toolchain, path, video_filter, ["-vsync", "0"], pattern)

    if completed.returncode:
        detail = completed.stderr.strip() or "unknown FFmpeg error"
        raise MediaError(f"could not extract thumbnails for {path.name}: {detail}")

    files = sorted(frames_dir.glob("*.jpg"))
    if not files:
        raise MediaError(f"no thumbnails were created for {path.name}")
    return files


def _run_ffmpeg(
    toolchain: Toolchain,
    path: Path,
    video_filter: str,
    sync_options: list[str],
    pattern: Path,
) -> subprocess.CompletedProcess:
    command = [
        toolchain.ffmpeg,
        "-hide_banner",
        "-y",
        "-i",
        str(path),
        "-vf",
        video_filter,
        *sync_options,
        "-q:v",
        "4",
        str(pattern),
    ]
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def prepare_browser_video(
    path: Path, toolchain: Toolchain, workdir: Path
) -> Path:
    """Return `path` when browsers can decode it, otherwise create an H.264 preview."""
    codec, pixel_format = _browser_video_format(path, toolchain)
    if (
        path.suffix.lower() in {".mp4", ".m4v"}
        and codec in _BROWSER_SAFE_CODECS
        and pixel_format in _BROWSER_SAFE_PIXEL_FORMATS
    ):
        return path

    print("Creating a browser-compatible full-resolution preview...")
    preview = workdir / "browser-preview.mp4"
    # Passthrough pacing keeps every VFR frame. The demuxer time base keeps each encoded
    # frame on its original PTS instead of rounding timestamps to the nominal frame rate.
    # Short GOPs make repeated browser seeks responsive without changing frame order.
    command = [
        toolchain.ffmpeg,
        "-hide_banner",
        "-y",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-fps_mode",
        "passthrough",
        "-enc_time_base",
        "demux",
        "-g",
        "12",
        "-keyint_min",
        "12",
        "-sc_threshold",
        "0",
        "-movflags",
        "+faststart",
        str(preview),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode:
        detail = completed.stderr.strip() or "unknown FFmpeg error"
        raise MediaError(f"could not create a browser-compatible preview: {detail}")
    if not preview.is_file():
        raise MediaError("could not create a browser-compatible preview")
    return preview


def _browser_video_format(path: Path, toolchain: Toolchain) -> tuple[str, str]:
    command = [
        toolchain.ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,pix_fmt",
        "-of",
        "json",
        str(path),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode:
        detail = completed.stderr.strip() or "unknown FFprobe error"
        raise MediaError(f"could not inspect video compatibility for {path.name}: {detail}")
    try:
        stream = json.loads(completed.stdout)["streams"][0]
        return str(stream["codec_name"]).lower(), str(stream["pix_fmt"]).lower()
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise MediaError(
            f"could not inspect video compatibility for {path.name}"
        ) from exc


def render_picker_html(video_name: str) -> str:
    """Return the local frame picker page without embedding frame timestamps."""
    safe_name = html.escape(video_name)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Frame picker | {safe_name}</title>
<style>
  :root {{
    color-scheme: dark;
    --ink: #080a0d;
    --panel: #11151a;
    --raised: #191f26;
    --line: #303945;
    --text: #eef1f4;
    --muted: #98a2ad;
    --accent: #f1ad3d;
    --accent-dark: #3a2a12;
    --success: #76c893;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    min-height: 100vh;
    background: var(--ink);
    color: var(--text);
    font-family: "Segoe UI", Arial, sans-serif;
  }}
  button, video {{ -webkit-tap-highlight-color: transparent; }}
  header {{
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 18px;
    padding: 14px 20px;
    border-bottom: 1px solid var(--line);
    background: var(--panel);
  }}
  h1 {{
    margin: 0;
    overflow: hidden;
    font-size: 16px;
    font-weight: 650;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}
  header p {{
    margin: 0;
    flex: 0 0 auto;
    color: var(--muted);
    font-family: Consolas, "SFMono-Regular", monospace;
    font-size: 11px;
    letter-spacing: .08em;
    text-transform: uppercase;
  }}
  main {{
    display: grid;
    justify-items: center;
    gap: 14px;
    padding: 18px 20px 0;
  }}
  .stage {{
    display: grid;
    width: 100%;
    min-height: 220px;
    place-items: center;
  }}
  video {{
    display: block;
    width: auto;
    max-width: 100%;
    max-height: 72vh;
    background: #000;
    box-shadow: 0 0 0 1px var(--line), 0 18px 55px rgba(0, 0, 0, .45);
  }}
  .readout {{
    display: flex;
    align-items: baseline;
    gap: 10px;
  }}
  #time {{
    font-family: Consolas, "SFMono-Regular", Menlo, monospace;
    font-size: clamp(42px, 7vw, 72px);
    font-variant-numeric: tabular-nums;
    font-weight: 700;
    letter-spacing: -.05em;
    line-height: .95;
  }}
  .unit {{
    color: var(--muted);
    font-family: Consolas, "SFMono-Regular", monospace;
    font-size: 12px;
    letter-spacing: .08em;
  }}
  .marks {{
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 8px;
  }}
  button {{
    min-height: 42px;
    border: 1px solid var(--line);
    border-radius: 4px;
    padding: 8px 12px;
    background: var(--raised);
    color: var(--text);
    font: 600 14px/1 "Segoe UI", Arial, sans-serif;
    cursor: pointer;
  }}
  button:hover {{ border-color: #596675; }}
  button:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
  button:disabled {{ cursor: not-allowed; opacity: .42; }}
  button kbd {{
    display: inline-grid;
    min-width: 22px;
    min-height: 22px;
    margin-right: 8px;
    place-items: center;
    border: 1px solid #4a5562;
    border-radius: 3px;
    background: #0d1014;
    color: var(--muted);
    font: 11px Consolas, monospace;
  }}
  .chip {{
    display: inline-block;
    min-width: 72px;
    margin-left: 10px;
    border-radius: 999px;
    padding: 5px 8px;
    background: #0d1014;
    color: var(--muted);
    font: 12px Consolas, monospace;
    font-variant-numeric: tabular-nums;
  }}
  .marked .chip {{ background: var(--accent-dark); color: var(--accent); }}
  #finish {{
    border-color: var(--accent);
    background: var(--accent);
    color: #171006;
  }}
  #hint, #status {{
    margin: 0;
    color: var(--muted);
    font-size: 12px;
    text-align: center;
  }}
  #status {{ min-height: 16px; color: var(--success); }}
  #filmstrip {{
    width: 100%;
    display: flex;
    gap: 6px;
    overflow-x: auto;
    padding: 10px 20px 14px;
    border-top: 1px solid var(--line);
    background: var(--panel);
    scrollbar-color: #4a5562 var(--panel);
  }}
  #filmstrip button {{
    position: relative;
    min-height: 0;
    flex: 0 0 auto;
    overflow: hidden;
    border: 2px solid transparent;
    border-radius: 2px;
    padding: 0;
    background: #000;
    opacity: .54;
  }}
  #filmstrip button.selected {{ border-color: var(--accent); opacity: 1; }}
  #filmstrip img {{ display: block; height: 140px; width: auto; }}
  #filmstrip span {{
    position: absolute;
    right: 5px;
    bottom: 5px;
    padding: 3px 5px;
    background: rgba(0, 0, 0, .78);
    color: #fff;
    font: 11px Consolas, monospace;
  }}
  .done {{
    display: grid;
    min-height: 100vh;
    place-items: center;
    padding: 30px;
    color: var(--success);
    font: 700 clamp(24px, 5vw, 48px) Consolas, monospace;
    text-align: center;
  }}
  @media (max-width: 640px) {{
    header {{ align-items: flex-start; flex-direction: column; gap: 4px; }}
    main {{ padding-inline: 10px; }}
    .marks {{ align-items: stretch; flex-direction: column; width: min(100%, 360px); }}
    #filmstrip {{ padding-inline: 10px; }}
  }}
  @media (prefers-reduced-motion: reduce) {{
    * {{ scroll-behavior: auto !important; }}
  }}
</style>
</head>
<body>
<header>
  <h1>{safe_name}</h1>
  <p>Exact frame timing</p>
</header>
<main id="picker">
  <div class="stage">
    <video id="video" src="/video" controls preload="metadata"></video>
  </div>
  <div class="readout"><span id="time">0.000</span><span class="unit">PTS seconds</span></div>
  <div class="marks">
    <button id="markLaunch" type="button"><kbd>L</kbd>Mark launch<span class="chip">Not marked</span></button>
    <button id="markHundred" type="button"><kbd>H</kbd>Mark 100 km/h<span class="chip">Not marked</span></button>
    <button id="finish" type="button" disabled>Finish</button>
  </div>
  <p id="hint">Space or click: play/pause &nbsp; Arrows: 1 frame &nbsp; Shift+arrows: 10 frames &nbsp; Home/End: first/last</p>
  <p id="status" role="status"></p>
</main>
<div id="filmstrip" aria-label="Video frames"></div>
<script>
  "use strict";
  const thumbnailLimit = {DEFAULT_THUMBNAIL_LIMIT};
  const video = document.getElementById("video");
  const timeEl = document.getElementById("time");
  const filmstrip = document.getElementById("filmstrip");
  const launchButton = document.getElementById("markLaunch");
  const hundredButton = document.getElementById("markHundred");
  const finishButton = document.getElementById("finish");
  const statusEl = document.getElementById("status");
  let times = [];
  let selected = 0;
  let requestedIndex = 0;
  let seekInFlight = false;
  let waitingForPaint = false;
  let thumbnailStep = 1;
  let selectedThumbnail = null;
  let launch = null;
  let hundred = null;
  let finished = false;

  function nearestIndex(value) {{
    let low = 0;
    let high = times.length - 1;
    while (low < high) {{
      const middle = Math.floor((low + high) / 2);
      if (times[middle] < value) low = middle + 1;
      else high = middle;
    }}
    if (low > 0 && Math.abs(times[low - 1] - value) <= Math.abs(times[low] - value)) return low - 1;
    return low;
  }}

  function showIndex(index) {{
    if (!times.length) return;
    selected = Math.max(0, Math.min(times.length - 1, index));
    timeEl.textContent = times[selected].toFixed(3);
    const thumbFrame = Math.min(
      times.length - 1,
      Math.round(selected / thumbnailStep) * thumbnailStep
    );
    const nextThumbnail = document.querySelector('[data-frame="' + thumbFrame + '"]');
    if (nextThumbnail !== selectedThumbnail) {{
      if (selectedThumbnail) selectedThumbnail.classList.remove("selected");
      selectedThumbnail = nextThumbnail;
      if (selectedThumbnail) {{
        selectedThumbnail.classList.add("selected");
        selectedThumbnail.scrollIntoView({{inline: "center", block: "nearest"}});
      }}
    }}
  }}

  function afterVideoPaint(callback) {{
    let finished = false;
    function finish() {{
      if (finished) return;
      finished = true;
      callback();
    }}
    if (typeof video.requestVideoFrameCallback === "function") {{
      video.requestVideoFrameCallback(finish);
      setTimeout(finish, 50);
    }} else {{
      requestAnimationFrame(finish);
    }}
  }}

  function pumpSeek() {{
    if (!times.length || seekInFlight || waitingForPaint) return;
    seekInFlight = true;
    video.currentTime = times[requestedIndex] + 0.002;
  }}

  function requestIndex(index) {{
    if (!times.length) return;
    requestedIndex = Math.max(0, Math.min(times.length - 1, index));
    video.pause();
    pumpSeek();
  }}

  function finishSeek() {{
    const landed = nearestIndex(video.currentTime);
    const wasQueuedSeek = seekInFlight;
    seekInFlight = false;
    if (!wasQueuedSeek) requestedIndex = landed;
    showIndex(landed);
    waitingForPaint = true;
    afterVideoPaint(function () {{
      waitingForPaint = false;
      if (landed !== requestedIndex) pumpSeek();
    }});
  }}

  function syncPlayback() {{
    if (!times.length || seekInFlight || waitingForPaint || video.paused) return;
    requestedIndex = nearestIndex(video.currentTime);
    showIndex(requestedIndex);
  }}

  function togglePlayback() {{
    if (video.paused) video.play().catch(function () {{}});
    else video.pause();
  }}

  function updateFinish() {{
    finishButton.disabled = launch === null || hundred === null;
  }}

  function mark(button, which) {{
    const value = times[selected];
    if (which === "launch") launch = value;
    else hundred = value;
    button.classList.add("marked");
    button.querySelector(".chip").textContent = value.toFixed(3);
    updateFinish();
  }}

  launchButton.addEventListener("click", function () {{ mark(launchButton, "launch"); }});
  hundredButton.addEventListener("click", function () {{ mark(hundredButton, "hundred"); }});
  video.addEventListener("click", togglePlayback);
  video.addEventListener("seeking", function () {{
    if (!seekInFlight) requestedIndex = nearestIndex(video.currentTime);
  }});
  video.addEventListener("seeked", finishSeek);
  video.addEventListener("timeupdate", syncPlayback);

  document.addEventListener("keydown", function (event) {{
    let destination = null;
    if (event.key === "ArrowLeft") destination = requestedIndex + (event.shiftKey ? -10 : -1);
    else if (event.key === "ArrowRight") destination = requestedIndex + (event.shiftKey ? 10 : 1);
    else if (event.key === "Home") destination = 0;
    else if (event.key === "End") destination = times.length - 1;
    else if (event.code === "Space") {{
      event.preventDefault();
      togglePlayback();
      return;
    }} else if (event.key.toLowerCase() === "l") {{
      event.preventDefault();
      mark(launchButton, "launch");
      return;
    }} else if (event.key.toLowerCase() === "h") {{
      event.preventDefault();
      mark(hundredButton, "hundred");
      return;
    }} else return;
    event.preventDefault();
    requestIndex(destination);
  }});

  finishButton.addEventListener("click", async function () {{
    finishButton.disabled = true;
    statusEl.textContent = "Sending marks...";
    try {{
      const response = await fetch("/done", {{
        method: "POST",
        headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{launch: launch, hundred: hundred}})
      }});
      if (!response.ok) throw new Error("The marks were not accepted.");
      finished = true;
      document.body.innerHTML = '<div class="done">Done. Back to the terminal.</div>';
    }} catch (error) {{
      statusEl.textContent = "Could not send the marks. Press Finish to try again.";
      updateFinish();
    }}
  }});

  window.addEventListener("pagehide", function () {{
    if (!finished && navigator.sendBeacon) {{
      navigator.sendBeacon("/cancel");
    }}
  }});

  async function loadTimes() {{
    try {{
      const response = await fetch("/times");
      if (!response.ok) throw new Error("Frame times unavailable.");
      times = await response.json();
      if (!times.length) throw new Error("No frame times found.");
      thumbnailStep = Math.max(1, Math.ceil(times.length / thumbnailLimit));
      let thumbnailNumber = 1;
      for (let frame = 0; frame < times.length; frame += thumbnailStep) {{
        const button = document.createElement("button");
        button.type = "button";
        button.dataset.frame = String(frame);
        button.setAttribute("aria-label", "Jump to frame at " + times[frame].toFixed(3) + " seconds");
        const image = document.createElement("img");
        image.loading = "lazy";
        image.src = "/thumbs/" + String(thumbnailNumber).padStart(6, "0") + ".jpg";
        image.alt = "";
        const label = document.createElement("span");
        label.textContent = times[frame].toFixed(3);
        button.append(image, label);
        button.addEventListener("click", function () {{
          requestIndex(frame);
        }});
        filmstrip.appendChild(button);
        thumbnailNumber += 1;
      }}
      requestIndex(0);
    }} catch (error) {{
      statusEl.textContent = "Could not load frame times. Close this page and try again.";
    }}
  }}

  loadTimes();
</script>
</body>
</html>
"""


def render_calibration_html(video_name: str) -> str:
    """Return the local gauge calibration page."""
    safe_name = html.escape(video_name)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gauge calibration | {safe_name}</title>
<style>
  :root {{ color-scheme: dark; --bg: #080a0d; --panel: #11151a; --line: #303945;
    --text: #eef1f4; --muted: #98a2ad; --accent: #f1ad3d; --ok: #76c893; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; min-height: 100vh; background: var(--bg); color: var(--text);
    font-family: "Segoe UI", Arial, sans-serif; }}
  header {{ display: flex; justify-content: space-between; gap: 16px; padding: 14px 20px;
    border-bottom: 1px solid var(--line); background: var(--panel); }}
  h1 {{ margin: 0; overflow: hidden; font-size: 16px; text-overflow: ellipsis;
    white-space: nowrap; }}
  header span {{ color: var(--muted); font: 11px Consolas, monospace; text-transform: uppercase; }}
  main {{ display: grid; justify-items: center; gap: 14px; padding: 18px; }}
  #frame {{ position: relative; display: inline-block; max-width: 100%; line-height: 0; }}
  video {{ display: block; width: auto; max-width: 100%; max-height: 72vh; background: #000;
    box-shadow: 0 0 0 1px var(--line), 0 18px 55px rgba(0,0,0,.45); }}
  canvas {{ position: absolute; inset: 0; width: 100%; height: 100%; cursor: crosshair; }}
  #time {{ font: 700 clamp(34px, 5vw, 56px) Consolas, monospace;
    font-variant-numeric: tabular-nums; }}
  #instruction {{ margin: 0; color: var(--accent); font-size: 18px; font-weight: 650; }}
  #steps {{ display: flex; flex-wrap: wrap; justify-content: center; gap: 8px; }}
  .step {{ border: 1px solid var(--line); border-radius: 4px; padding: 8px 11px;
    background: var(--panel); color: var(--muted); }}
  .step.active {{ border-color: var(--accent); color: var(--accent); }}
  .step.done {{ color: var(--ok); }}
  #actions {{ display: flex; flex-wrap: wrap; justify-content: center; gap: 8px; }}
  button {{ min-height: 40px; border: 1px solid var(--line); border-radius: 4px;
    padding: 8px 14px; background: #191f26; color: var(--text); font-weight: 650;
    cursor: pointer; }}
  button:disabled {{ cursor: not-allowed; opacity: .4; }}
  #finish {{ border-color: var(--accent); background: var(--accent); color: #171006; }}
  #hint, #status {{ margin: 0; color: var(--muted); font-size: 12px; text-align: center; }}
  #status {{ min-height: 16px; color: var(--ok); }}
  .done-page {{ display: grid; min-height: 100vh; place-items: center; padding: 30px;
    color: var(--ok); font: 700 clamp(24px, 5vw, 48px) Consolas, monospace; text-align: center; }}
</style>
</head>
<body>
<header><h1>{safe_name}</h1><span>Gauge calibration</span></header>
<main>
  <div id="frame">
    <video id="video" src="/video" preload="metadata" playsinline></video>
    <canvas id="overlay" aria-label="Gauge calibration points"></canvas>
  </div>
  <div id="time">0.000</div>
  <p id="instruction">Choose a stopped frame, then click the needle pivot</p>
  <div id="steps">
    <div class="step" data-step="0">1. Click the needle pivot</div>
    <div class="step" data-step="1">2. Click the needle tip at zero</div>
    <div class="step" data-step="2">3. Click the 100 km/h mark</div>
  </div>
  <div id="actions">
    <button id="undo" type="button" disabled>Undo point</button>
    <button id="reset" type="button" disabled>Reset</button>
    <button id="finish" type="button" disabled>Finish calibration</button>
  </div>
  <p id="hint">Space: play/pause &nbsp; Arrows: 1 frame &nbsp; Shift+arrows: 10 frames &nbsp; Home/End: first/last</p>
  <p id="status" role="status"></p>
</main>
<script>
  "use strict";
  const video = document.getElementById("video");
  const canvas = document.getElementById("overlay");
  const context = canvas.getContext("2d");
  const timeEl = document.getElementById("time");
  const instructionEl = document.getElementById("instruction");
  const statusEl = document.getElementById("status");
  const undoButton = document.getElementById("undo");
  const resetButton = document.getElementById("reset");
  const finishButton = document.getElementById("finish");
  const instructions = [
    "Click the needle pivot",
    "Click the needle tip at zero",
    "Click the 100 km/h mark",
    "Calibration points ready"
  ];
  const colors = ["#f1ad3d", "#76c893", "#64b5f6"];
  let times = [];
  let selected = 0;
  let points = [];
  let calibrationFrame = null;
  let finished = false;

  function nearestIndex(value) {{
    let low = 0;
    let high = times.length - 1;
    while (low < high) {{
      const middle = Math.floor((low + high) / 2);
      if (times[middle] < value) low = middle + 1;
      else high = middle;
    }}
    if (low > 0 && Math.abs(times[low - 1] - value) <= Math.abs(times[low] - value)) return low - 1;
    return low;
  }}

  function requestIndex(index) {{
    if (!times.length) return;
    if (points.length) resetPoints("Points reset because the frame changed.");
    selected = Math.max(0, Math.min(times.length - 1, index));
    video.pause();
    video.currentTime = times[selected] + 0.002;
    timeEl.textContent = times[selected].toFixed(3);
  }}

  function sizeCanvas() {{
    const rect = video.getBoundingClientRect();
    const scale = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.round(rect.width * scale));
    canvas.height = Math.max(1, Math.round(rect.height * scale));
    drawPoints();
  }}

  function drawPoints() {{
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.lineWidth = Math.max(2, canvas.width / 300);
    if (points.length > 1) {{
      context.strokeStyle = "rgba(241,173,61,.8)";
      for (let index = 1; index < points.length; index += 1) {{
        context.beginPath();
        context.moveTo(points[0][0] * canvas.width, points[0][1] * canvas.height);
        context.lineTo(points[index][0] * canvas.width, points[index][1] * canvas.height);
        context.stroke();
      }}
    }}
    points.forEach(function (point, index) {{
      context.beginPath();
      context.fillStyle = colors[index];
      context.strokeStyle = "#080a0d";
      context.arc(point[0] * canvas.width, point[1] * canvas.height,
        Math.max(6, canvas.width / 90), 0, Math.PI * 2);
      context.fill();
      context.stroke();
    }});
  }}

  function updateState() {{
    instructionEl.textContent = instructions[points.length];
    document.querySelectorAll(".step").forEach(function (step, index) {{
      step.classList.toggle("done", index < points.length);
      step.classList.toggle("active", index === points.length);
    }});
    undoButton.disabled = points.length === 0;
    resetButton.disabled = points.length === 0;
    finishButton.disabled = points.length !== 3;
    drawPoints();
  }}

  function resetPoints(message) {{
    points = [];
    calibrationFrame = null;
    statusEl.textContent = message || "";
    updateState();
  }}

  canvas.addEventListener("click", function (event) {{
    if (!times.length || points.length === 3) return;
    video.pause();
    const rect = canvas.getBoundingClientRect();
    const x = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    const y = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
    if (points.length === 0) calibrationFrame = times[selected];
    points.push([x, y]);
    statusEl.textContent = "";
    updateState();
  }});

  undoButton.addEventListener("click", function () {{
    points.pop();
    if (!points.length) calibrationFrame = null;
    statusEl.textContent = "";
    updateState();
  }});
  resetButton.addEventListener("click", function () {{ resetPoints(""); }});
  video.addEventListener("loadedmetadata", sizeCanvas);
  video.addEventListener("resize", sizeCanvas);
  window.addEventListener("resize", sizeCanvas);
  video.addEventListener("seeked", function () {{
    if (!times.length) return;
    selected = nearestIndex(video.currentTime);
    timeEl.textContent = times[selected].toFixed(3);
  }});

  document.addEventListener("keydown", function (event) {{
    let destination = null;
    if (event.key === "ArrowLeft") destination = selected + (event.shiftKey ? -10 : -1);
    else if (event.key === "ArrowRight") destination = selected + (event.shiftKey ? 10 : 1);
    else if (event.key === "Home") destination = 0;
    else if (event.key === "End") destination = times.length - 1;
    else if (event.code === "Space") {{
      event.preventDefault();
      if (points.length) resetPoints("Points reset before playback.");
      if (video.paused) video.play().catch(function () {{}});
      else video.pause();
      return;
    }} else return;
    event.preventDefault();
    requestIndex(destination);
  }});

  finishButton.addEventListener("click", async function () {{
    finishButton.disabled = true;
    statusEl.textContent = "Sending calibration...";
    try {{
      const response = await fetch("/calibrate", {{
        method: "POST",
        headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{
          pivot: points[0], zero: points[1], hundred: points[2], frame: calibrationFrame
        }})
      }});
      if (!response.ok) throw new Error("Calibration was not accepted.");
      finished = true;
      document.body.innerHTML = '<div class="done-page">Calibration saved. Back to the terminal.</div>';
    }} catch (error) {{
      statusEl.textContent = "Could not send calibration. Press Finish calibration to try again.";
      updateState();
    }}
  }});

  window.addEventListener("pagehide", function () {{
    if (!finished && navigator.sendBeacon) navigator.sendBeacon("/cancel");
  }});

  async function loadTimes() {{
    try {{
      const response = await fetch("/times");
      if (!response.ok) throw new Error("Frame times unavailable.");
      times = await response.json();
      if (!times.length) throw new Error("No frame times found.");
      requestIndex(0);
      updateState();
    }} catch (error) {{
      statusEl.textContent = "Could not load frame times. Close this page and try again.";
    }}
  }}

  loadTimes();
</script>
</body>
</html>
"""


class _PickerServer:
    """Serve one picker session on a loopback-only ephemeral port."""

    def __init__(
        self,
        video_path: Path,
        workdir: Path,
        times: list[float],
        *,
        video_name: str | None = None,
    ) -> None:
        self.video_path = video_path.resolve()
        self.video_name = video_name or video_path.name
        self.frames_dir = (workdir / "frames").resolve()
        self.times = list(times)
        self.result_event = threading.Event()
        self.result: tuple[float, float] | None = None
        self.cancelled = False
        self._thread: threading.Thread | None = None

        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                owner._handle_get(self)

            def do_POST(self) -> None:
                owner._handle_post(self)

            def log_message(self, format: str, *args: object) -> None:
                return

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = int(self._httpd.server_address[1])
        self.url = f"http://127.0.0.1:{self.port}/"

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="zero2hundred-picker",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if self._thread is not None:
            self._httpd.shutdown()
            self._thread.join(timeout=2)
            self._thread = None
        self._httpd.server_close()

    def _handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        path = unquote(urlsplit(handler.path).path)
        if path == "/":
            payload = render_picker_html(self.video_name).encode("utf-8")
            self._send_bytes(handler, 200, payload, "text/html; charset=utf-8")
        elif path == "/times":
            payload = json.dumps(self.times, separators=(",", ":")).encode("utf-8")
            self._send_bytes(handler, 200, payload, "application/json")
        elif path == "/video":
            self._serve_video(handler)
        elif path.startswith("/thumbs/"):
            self._serve_thumbnail(handler, path.removeprefix("/thumbs/"))
        else:
            handler.send_error(404)

    def _handle_post(self, handler: BaseHTTPRequestHandler) -> None:
        path = unquote(urlsplit(handler.path).path)
        if path == "/cancel":
            self.cancelled = True
            try:
                handler.send_response(204)
                handler.send_header("Content-Length", "0")
                handler.end_headers()
            finally:
                self.result_event.set()
            return
        if path != "/done":
            handler.send_error(404)
            return

        try:
            length = int(handler.headers.get("Content-Length", ""))
            if length < 0 or length > 64 * 1024:
                raise ValueError
            raw = handler.rfile.read(length)
            data = json.loads(
                raw.decode("utf-8"),
                parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
            )
            launch = data["launch"]
            hundred = data["hundred"]
            if not _valid_mark(launch) or not _valid_mark(hundred):
                raise ValueError
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(handler, 400, {"ok": False})
            return

        self.result = (float(launch), float(hundred))
        try:
            self._send_json(handler, 200, {"ok": True})
        finally:
            self.result_event.set()

    def _serve_video(self, handler: BaseHTTPRequestHandler) -> None:
        size = self.video_path.stat().st_size
        range_header = handler.headers.get("Range")
        if range_header is None:
            start, end, status = 0, max(0, size - 1), 200
        else:
            parsed = _parse_byte_range(range_header, size)
            if parsed is None:
                handler.send_response(416)
                handler.send_header("Content-Range", f"bytes */{size}")
                handler.send_header("Accept-Ranges", "bytes")
                handler.send_header("Content-Length", "0")
                handler.end_headers()
                return
            start, end = parsed
            status = 206

        length = 0 if size == 0 else end - start + 1
        handler.send_response(status)
        handler.send_header("Content-Type", "video/mp4")
        handler.send_header("Accept-Ranges", "bytes")
        handler.send_header("Content-Length", str(length))
        if status == 206:
            handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        handler.end_headers()

        if length == 0:
            return
        with self.video_path.open("rb") as video:
            video.seek(start)
            remaining = length
            while remaining:
                chunk = video.read(min(_CHUNK_SIZE, remaining))
                if not chunk:
                    break
                try:
                    handler.wfile.write(chunk)
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
                    return
                remaining -= len(chunk)

    def _serve_thumbnail(self, handler: BaseHTTPRequestHandler, name: str) -> None:
        candidate = (self.frames_dir / name).resolve()
        if (
            candidate.parent != self.frames_dir
            or candidate.suffix.lower() != ".jpg"
            or not candidate.is_file()
        ):
            handler.send_error(404)
            return
        self._send_bytes(handler, 200, candidate.read_bytes(), "image/jpeg")

    @staticmethod
    def _send_bytes(
        handler: BaseHTTPRequestHandler,
        status: int,
        payload: bytes,
        content_type: str,
    ) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        try:
            handler.wfile.write(payload)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            return

    @classmethod
    def _send_json(
        cls, handler: BaseHTTPRequestHandler, status: int, value: dict[str, bool]
    ) -> None:
        payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
        cls._send_bytes(handler, status, payload, "application/json")


class _CalibrationServer(_PickerServer):
    """Serve one gauge calibration session on the loopback interface."""

    def __init__(
        self,
        video_path: Path,
        workdir: Path,
        times: list[float],
        *,
        video_name: str | None = None,
    ) -> None:
        super().__init__(video_path, workdir, times, video_name=video_name)
        self.calibration_result = None

    def _handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        path = unquote(urlsplit(handler.path).path)
        if path == "/":
            payload = render_calibration_html(self.video_name).encode("utf-8")
            self._send_bytes(handler, 200, payload, "text/html; charset=utf-8")
            return
        super()._handle_get(handler)

    def _handle_post(self, handler: BaseHTTPRequestHandler) -> None:
        path = unquote(urlsplit(handler.path).path)
        if path == "/cancel":
            super()._handle_post(handler)
            return
        if path != "/calibrate":
            handler.send_error(404)
            return

        try:
            length = int(handler.headers.get("Content-Length", ""))
            if length < 0 or length > 64 * 1024:
                raise ValueError
            raw = handler.rfile.read(length)
            from zero2hundred.detect.needle import Calibration

            calibration = Calibration.from_json(raw.decode("utf-8"))
        except (TypeError, ValueError, UnicodeDecodeError):
            self._send_json(handler, 400, {"ok": False})
            return

        self.calibration_result = calibration
        try:
            self._send_json(handler, 200, {"ok": True})
        finally:
            self.result_event.set()


def _valid_mark(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _parse_byte_range(value: str, size: int) -> tuple[int, int] | None:
    match = _RANGE_PATTERN.fullmatch(value.strip())
    if match is None or size <= 0:
        return None
    start_text, end_text = match.groups()
    if not start_text and not end_text:
        return None
    if not start_text:
        suffix_length = int(end_text)
        if suffix_length <= 0:
            return None
        return max(0, size - suffix_length), size - 1
    start = int(start_text)
    if start >= size:
        return None
    end = size - 1 if not end_text else min(int(end_text), size - 1)
    if end < start:
        return None
    return start, end


def serve_calibration(
    path: Path,
    toolchain: Toolchain,
    times: list[float],
    workdir: Path,
) -> "Calibration":
    """Open a local gauge calibration page and return its three marked points."""
    browser_video = prepare_browser_video(path, toolchain, workdir)
    server = _CalibrationServer(
        browser_video,
        workdir,
        times,
        video_name=path.name,
    )
    server.start()
    try:
        webbrowser.open(server.url)
        while not server.result_event.wait(0.1):
            pass
        if server.cancelled:
            raise KeyboardInterrupt
        if server.calibration_result is None:
            raise MediaError("calibration page closed without returning points")
        return server.calibration_result
    finally:
        server.stop()


def serve_picker(
    path: Path, toolchain: Toolchain, times: list[float], workdir: Path
) -> tuple[float, float]:
    """Extract thumbnails, open a local picker, and wait for both frame marks."""
    count = len(times)
    step = _step_for(count, DEFAULT_THUMBNAIL_LIMIT)
    thumbnails = extract_thumbnails(path, toolchain, step, workdir)
    expected = len(thumbnail_indices(count))
    if len(thumbnails) != expected:
        print(
            f"Warning: expected {expected} thumbnails but ffmpeg produced "
            f"{len(thumbnails)}; picker times near the end of the clip may be slightly off.",
            file=sys.stderr,
        )

    browser_video = prepare_browser_video(path, toolchain, workdir)
    server = _PickerServer(browser_video, workdir, times, video_name=path.name)
    server.start()
    try:
        webbrowser.open(server.url)
        while not server.result_event.wait(0.1):
            pass
        if server.cancelled:
            raise KeyboardInterrupt
        if server.result is None:
            raise MediaError("frame picker closed without returning marks")
        return server.result
    finally:
        server.stop()
