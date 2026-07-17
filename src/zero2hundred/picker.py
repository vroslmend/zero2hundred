from __future__ import annotations

import base64
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
from zero2hundred.progress import ProgressReporter, stream_ffmpeg_progress

DEFAULT_THUMBNAIL_LIMIT = 1200
_CHUNK_SIZE = 64 * 1024
_RANGE_PATTERN = re.compile(r"bytes=(\d*)-(\d*)$")
_BROWSER_SAFE_CODECS = {"h264", "vp8", "vp9", "av1"}
_BROWSER_SAFE_PIXEL_FORMATS = {"yuv420p", "yuvj420p"}
_MANROPE_FONT = Path(__file__).with_name("assets") / "Manrope-Variable.ttf"
# A forward-leaning "z" monogram with a speed-tail, ivory on a carbon tile. The
# same mark is drawn inline in the header and base64-encoded as the tab favicon,
# so both load without any external request.
_MARK_INNER = (
    '<rect x="1.5" y="1.5" width="29" height="29" rx="8" fill="#0e1012" stroke="#2b2f33"/>'
    '<g transform="translate(16 16) skewX(-13) translate(-16 -16)">'
    '<path d="M9 10.5H23L9 21" fill="none" stroke="#f2f2ef" stroke-width="2.8"'
    ' stroke-linecap="round" stroke-linejoin="round"/>'
    '<path d="M9 19.7 9 22.7 26 21.35 26 21.05Z" fill="#f2f2ef"/>'
    "</g>"
)
# The inline header copy omits the SVG xmlns (the HTML parser supplies it) so the
# page holds no literal external URL; the standalone favicon needs the xmlns, but
# base64 encoding keeps it out of the page source.
_LOGO_SVG = f'<svg viewBox="0 0 32 32" class="brand-mark" aria-hidden="true">{_MARK_INNER}</svg>'
_FAVICON = "data:image/svg+xml;base64," + base64.b64encode(
    f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">{_MARK_INNER}</svg>'.encode()
).decode("ascii")


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
    path: Path, toolchain: Toolchain, workdir: Path, *, duration: float = 0.0
) -> Path:
    """Return `path` when browsers can decode it, otherwise create an H.264 preview.

    When `duration` is positive the transcode reports percentage progress; with
    an unknown duration it runs silently.
    """
    codec, pixel_format, rotation = _browser_video_format(path, toolchain)
    if (
        path.suffix.lower() in {".mp4", ".m4v"}
        and codec in _BROWSER_SAFE_CODECS
        and pixel_format in _BROWSER_SAFE_PIXEL_FORMATS
        and rotation % 360 == 0
    ):
        return path

    print("  Creating a browser-compatible full-resolution preview...")
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
    ]
    if duration > 0:
        reporter = ProgressReporter()
        stream_ffmpeg_progress(
            command + ["-progress", "pipe:1", "-nostats", str(preview)],
            duration,
            reporter,
            error_prefix="could not create a browser-compatible preview: ",
        )
        reporter(1.0)
        reporter.finish()
    else:
        completed = subprocess.run(
            command + [str(preview)],
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


def _browser_video_format(path: Path, toolchain: Toolchain) -> tuple[str, str, int]:
    command = [
        toolchain.ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,pix_fmt:stream_tags=rotate:stream_side_data=rotation",
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
        rotation = 0
        for side_data in stream.get("side_data_list", []):
            if "rotation" in side_data:
                rotation = int(float(side_data["rotation"]))
                break
        else:
            rotation = int(float(stream.get("tags", {}).get("rotate", 0)))
        return (
            str(stream["codec_name"]).lower(),
            str(stream["pix_fmt"]).lower(),
            rotation,
        )
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
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
<link rel="icon" href="{_FAVICON}">
<title>{safe_name} · zero2hundred</title>
<style>
  @font-face {{
    font-family: "Manrope";
    src: url("/font") format("truetype");
    font-style: normal;
    font-weight: 200 800;
    font-display: swap;
  }}
  :root {{
    color-scheme: dark;
    --carbon: #090a0b;
    --dash: #111315;
    --raised: #181b1e;
    --etched: #2b2f33;
    --ivory: #f2f2ef;
    --muted: #92979d;
    --soft: #c2c5c8;
    --font-ui: "Manrope", "Segoe UI", Arial, sans-serif;
    --font-display: "Manrope", "Segoe UI", Arial, sans-serif;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    display: grid;
    grid-template-rows: auto minmax(0, 1fr) auto;
    height: 100vh;
    min-height: 100vh;
    overflow: hidden;
    background: var(--carbon);
    color: var(--ivory);
    font-family: var(--font-ui);
  }}
  button, video {{ -webkit-tap-highlight-color: transparent; }}
  header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 18px;
    padding: 14px 20px;
    border-bottom: 1px solid var(--etched);
    background: var(--dash);
  }}
  h1 {{
    display: flex;
    align-items: center;
    gap: 9px;
    min-width: 0;
    margin: 0;
    font-size: 15px;
    font-weight: 600;
  }}
  .brand-mark {{ width: 22px; height: 22px; flex: 0 0 auto; }}
  .brand-name {{ color: var(--ivory); letter-spacing: -.01em; white-space: nowrap; }}
  .brand-file {{
    min-width: 0;
    overflow: hidden;
    color: var(--muted);
    font-weight: 450;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}
  .brand-file::before {{ content: "·"; margin: 0 8px 0 2px; color: var(--etched); }}
  header p {{
    margin: 0;
    flex: 0 0 auto;
    color: var(--muted);
    font-size: 13px;
    font-weight: 450;
  }}
  footer.credit {{
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 12px;
    padding: 6px 20px;
    border-top: 1px solid var(--etched);
    background: var(--dash);
    color: var(--muted);
    font-size: 11px;
  }}
  footer.credit a {{
    color: var(--soft);
    text-decoration: none;
    border-bottom: 1px solid var(--etched);
    transition: color 120ms ease, border-color 120ms ease;
  }}
  footer.credit a:hover {{ color: var(--ivory); border-color: var(--soft); }}
  main {{ min-height: 0; }}
  .stage {{
    position: relative;
    display: grid;
    width: 100%;
    min-width: 0;
    min-height: 240px;
    overflow: hidden;
    border: 1px solid var(--etched);
    background: #000;
    place-items: center;
  }}
  video {{
    position: absolute;
    inset: 0;
    display: block;
    width: 100%;
    height: 100%;
    object-fit: contain;
    background: #000;
    transform: scale(1);
    transform-origin: center bottom;
  }}
  .stage.gauge-view video {{ cursor: grab; touch-action: none; }}
  .stage.dragging video {{ cursor: grabbing; }}
  .view-controls {{
    position: absolute;
    z-index: 2;
    top: 12px;
    right: 12px;
    display: flex;
    gap: 6px;
  }}
  .view-controls button {{
    min-height: 36px;
    border-color: rgba(255, 255, 255, .24);
    background: rgba(9, 11, 12, .82);
    backdrop-filter: blur(8px);
    color: #fff;
    font-size: 12.5px;
  }}
  [hidden] {{ display: none !important; }}
  #picker {{
    display: grid;
    grid-template-columns: minmax(0, 1fr) 290px;
    gap: 14px;
    height: 100%;
    padding: 14px;
  }}
  .viewer {{
    display: grid;
    min-width: 0;
    min-height: 0;
    grid-template-rows: minmax(0, 1fr) auto;
    gap: 10px;
  }}
  .transport {{
    display: grid;
    grid-template-columns: minmax(170px, 1fr) auto;
    align-items: center;
    gap: 12px 20px;
    min-height: 94px;
    padding: 12px 14px;
    border: 1px solid var(--etched);
    background: var(--dash);
  }}
  .transport-data {{ display: grid; gap: 9px; }}
  .readout {{ display: flex; align-items: baseline; gap: 9px; }}
  #time {{
    font-family: var(--font-display);
    font-size: clamp(32px, 4.1vw, 54px);
    font-variant-numeric: tabular-nums;
    font-weight: 620;
    line-height: .9;
  }}
  .unit {{
    color: var(--muted);
    font-size: 12.5px;
    font-weight: 450;
  }}
  .frame-meta {{ display: flex; align-items: baseline; gap: 8px; }}
  .frame-meta span {{ color: var(--muted); font-size: 12px; font-weight: 450; }}
  #frameCount {{
    color: var(--soft);
    font: 600 13px/1 var(--font-display);
    font-variant-numeric: tabular-nums;
  }}
  .transport-buttons {{ display: flex; align-items: center; gap: 5px; }}
  button {{
    min-height: 42px;
    border: 1px solid var(--etched);
    border-radius: 4px;
    padding: 8px 12px;
    background: var(--raised);
    color: var(--ivory);
    font: 600 13.5px/1 var(--font-ui);
    cursor: pointer;
  }}
  button:hover {{ border-color: #5a5f64; background: #202326; }}
  button:focus-visible {{ outline: 2px solid var(--ivory); outline-offset: 2px; }}
  button:disabled {{ cursor: not-allowed; opacity: .42; }}
  .transport-buttons button {{
    min-width: 44px;
    padding-inline: 10px;
    font-family: var(--font-display);
    font-size: 14px;
    font-variant-numeric: tabular-nums;
  }}
  #playPause {{ min-width: 88px; color: var(--ivory); }}
  button kbd {{
    display: inline-grid;
    min-width: 22px;
    min-height: 22px;
    margin-right: 8px;
    place-items: center;
    border: 1px solid #3b4045;
    border-radius: 4px;
    background: #0d0f10;
    color: var(--muted);
    font: 600 11px/1 var(--font-ui);
  }}
  .shortcut-guide {{
    grid-column: 1 / -1;
    display: flex;
    flex-wrap: wrap;
    gap: 8px 18px;
    padding-top: 11px;
    border-top: 1px solid var(--etched);
  }}
  .shortcut, .panel-shortcut {{
    display: inline-flex;
    align-items: center;
    gap: 7px;
    color: var(--muted);
    font-size: 12px;
    line-height: 1.3;
  }}
  .shortcut kbd, .panel-shortcut kbd {{
    display: inline-grid;
    min-width: 24px;
    min-height: 22px;
    padding: 3px 6px;
    place-items: center;
    border: 1px solid #3b4045;
    border-radius: 4px;
    background: #0d0f10;
    color: var(--soft);
    font: 600 11px/1 var(--font-ui);
    white-space: nowrap;
  }}
  .action-label {{
    color: var(--soft);
    font-size: 11.5px;
    font-weight: 650;
  }}
  .timing-panel {{
    display: flex;
    min-height: 0;
    flex-direction: column;
    padding: 16px;
    border: 1px solid var(--etched);
    background: var(--dash);
    overflow-y: auto;
  }}
  .panel-heading {{
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 10px;
    padding-bottom: 10px;
    border-bottom: 1px solid var(--etched);
  }}
  .panel-heading strong {{ font-size: 16px; font-weight: 620; }}
  .endpoint {{ display: grid; gap: 7px; padding: 12px 0; }}
  .mark-control {{
    display: grid;
    min-height: 68px;
    grid-template-columns: 1fr;
    gap: 9px;
    align-content: center;
    text-align: left;
  }}
  .mark-label {{ display: flex; align-items: center; font-size: 14px; font-weight: 600; }}
  .mark-value {{
    color: var(--muted);
    font: 580 19px/1 var(--font-display);
    font-variant-numeric: tabular-nums;
  }}
  .mark-control {{ border-left: 2px solid #555a60; }}
  .mark-control.marked {{ border-color: var(--soft); background: #1c1f22; }}
  .mark-control.marked .mark-value {{ color: var(--ivory); }}
  .jump {{ min-height: 32px; padding: 6px 10px; color: var(--muted); font-size: 12px; }}
  .interval {{
    display: grid;
    grid-template-columns: 16px 1fr;
    align-items: center;
    gap: 10px;
    min-height: 58px;
    padding: 0 4px;
  }}
  .interval-line {{
    position: relative;
    width: 1px;
    height: 48px;
    justify-self: center;
    background: var(--etched);
  }}
  .interval-line::before, .interval-line::after {{
    position: absolute;
    left: -3px;
    width: 7px;
    height: 7px;
    border: 1px solid var(--soft);
    border-radius: 50%;
    background: var(--dash);
    content: "";
  }}
  .interval-line::before {{ top: -1px; }}
  .interval-line::after {{ bottom: -1px; }}
  .interval-copy {{ display: grid; gap: 3px; }}
  .interval-copy span {{ color: var(--muted); font-size: 12.5px; font-weight: 500; }}
  #elapsed {{ font: 620 23px/1 var(--font-display); font-variant-numeric: tabular-nums; }}
  .panel-actions {{ display: grid; gap: 7px; margin-top: auto; padding-top: 10px; }}
  #finish {{
    min-height: 50px;
    font-size: 14px;
  }}
  #finish:not(:disabled) {{
    border-color: var(--ivory);
    background: var(--ivory);
    color: var(--carbon);
  }}
  #finish:not(:disabled):hover {{ background: #fff; }}
  .panel-guide {{
    display: grid;
    gap: 9px;
    margin-top: 4px;
    padding-top: 14px;
    border-top: 1px solid var(--etched);
  }}
  .panel-guide > strong {{ color: var(--soft); font-size: 12.5px; font-weight: 600; }}
  .panel-shortcuts {{ display: flex; flex-wrap: wrap; gap: 8px 14px; }}
  .replace-note, #status {{
    margin: 0;
    color: var(--muted);
    font-size: 12px;
    line-height: 1.45;
  }}
  #status {{ min-height: 16px; color: var(--ivory); }}
  #filmstrip {{
    width: 100%;
    display: flex;
    gap: 6px;
    overflow-x: auto;
    padding: 8px 14px 11px;
    border-top: 1px solid var(--etched);
    background: var(--dash);
    scrollbar-color: #4b5055 var(--dash);
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
  #filmstrip button.selected {{ border-color: var(--ivory); opacity: 1; }}
  #filmstrip button.launch-mark {{ box-shadow: inset 0 3px var(--soft); }}
  #filmstrip button.hundred-mark {{ box-shadow: inset 0 -3px var(--ivory); }}
  #filmstrip img {{ display: block; height: 94px; width: auto; }}
  #filmstrip span {{
    position: absolute;
    right: 5px;
    bottom: 5px;
    padding: 3px 5px;
    background: rgba(0, 0, 0, .78);
    color: #fff;
    font: 550 12px/1 var(--font-display);
    font-variant-numeric: tabular-nums;
  }}
  .done {{
    display: grid;
    min-height: 100vh;
    place-items: center;
    padding: 30px;
    text-align: center;
  }}
  .done-copy {{ display: grid; gap: 9px; }}
  .done-copy strong {{
    color: var(--ivory);
    font: 600 clamp(28px, 4vw, 44px)/1.1 var(--font-display);
  }}
  .done-copy span {{ color: var(--muted); font-size: 14px; }}
  @media (max-width: 900px) {{
    body {{ height: auto; min-height: 100vh; overflow: auto; }}
    header {{ align-items: flex-start; flex-direction: column; gap: 4px; }}
    #picker {{ grid-template-columns: 1fr; height: auto; padding: 10px; }}
    .stage {{ height: min(60vh, 620px); }}
    .timing-panel {{ min-height: 440px; }}
    #filmstrip {{ padding-inline: 10px; }}
  }}
  @media (max-width: 560px) {{
    .transport {{ grid-template-columns: 1fr; }}
    .transport-buttons {{ justify-content: space-between; }}
    .transport-buttons button {{ min-width: 40px; }}
  }}
  @media (max-height: 760px) {{
    body {{ height: auto; min-height: 100vh; overflow: auto; }}
    #picker {{ height: auto; }}
    .stage {{ min-height: 320px; }}
  }}
  @media (prefers-reduced-motion: reduce) {{
    * {{ scroll-behavior: auto !important; }}
  }}
</style>
</head>
<body>
<header>
  <h1>{_LOGO_SVG}<span class="brand-name">zero2hundred</span><span class="brand-file">{safe_name}</span></h1>
  <p>Frame picker</p>
</header>
<main id="picker">
  <section class="viewer" aria-label="Video and frame controls">
    <div class="stage">
      <video id="video" src="/video" preload="metadata" playsinline aria-label="Video preview"></video>
      <div class="view-controls">
        <button id="resetView" type="button" hidden>Reset view</button>
        <button id="viewMode" type="button" aria-pressed="false">Gauge view</button>
      </div>
    </div>
    <div class="transport">
      <div class="transport-data">
        <div class="readout"><span id="time">0.000</span><span class="unit">seconds</span></div>
        <div class="frame-meta"><span>Current frame</span><strong id="frameCount">- / -</strong></div>
      </div>
      <div class="transport-buttons" aria-label="Frame transport">
        <button id="stepBackTen" type="button" title="Back 10 frames">-10</button>
        <button id="stepBack" type="button" title="Back 1 frame">-1</button>
        <button id="playPause" type="button">Play</button>
        <button id="stepForward" type="button" title="Forward 1 frame">+1</button>
        <button id="stepForwardTen" type="button" title="Forward 10 frames">+10</button>
      </div>
      <div class="shortcut-guide" aria-label="Playback shortcuts">
        <span class="shortcut"><kbd>Space</kbd><span>Play or pause</span></span>
        <span class="shortcut"><kbd>← →</kbd><span>Tap for one frame, hold to move</span></span>
        <span class="shortcut"><kbd>Shift</kbd><span>Skip ten frames</span></span>
        <span class="shortcut"><kbd>Z</kbd><span>Gauge view</span></span>
        <span id="gaugeHint" class="shortcut" hidden><span class="action-label">Drag</span><span>Reposition gauge</span></span>
      </div>
    </div>
  </section>
  <aside class="timing-panel" aria-label="Run timing marks">
    <div class="panel-heading"><strong>Timing</strong></div>
    <div class="endpoint launch-endpoint">
      <button id="markLaunch" class="mark-control" type="button" disabled>
        <span class="mark-label"><kbd>L</kbd>Mark launch</span>
        <span class="mark-value">Not marked</span>
      </button>
      <button id="jumpLaunch" class="jump" type="button" disabled>Go to launch frame</button>
    </div>
    <div class="interval">
      <span class="interval-line" aria-hidden="true"></span>
      <div class="interval-copy"><span>Run time</span><strong id="elapsed">--.---</strong></div>
    </div>
    <div class="endpoint hundred-endpoint">
      <button id="markHundred" class="mark-control" type="button" disabled>
        <span class="mark-label"><kbd>H</kbd>Mark 100 km/h</span>
        <span class="mark-value">Not marked</span>
      </button>
      <button id="jumpHundred" class="jump" type="button" disabled>Go to 100 km/h frame</button>
    </div>
    <div class="panel-guide" aria-label="Marking shortcuts">
      <strong>Keyboard shortcuts</strong>
      <div class="panel-shortcuts">
        <span class="panel-shortcut"><kbd>L</kbd><span>Mark launch</span></span>
        <span class="panel-shortcut"><kbd>H</kbd><span>Mark 100 km/h</span></span>
      </div>
      <p class="replace-note">Mark a frame again to replace it.</p>
    </div>
    <div class="panel-actions">
      <button id="finish" type="button" disabled>Use these frames</button>
      <p id="status" role="status"></p>
    </div>
  </aside>
</main>
<div id="filmstrip" aria-label="Video frames"></div>
<footer class="credit">
  <span>Made by <a href="https://github.com/vroslmend" target="_blank" rel="noopener">Ammar Hassan</a></span>
</footer>
<script>
  "use strict";
  const thumbnailLimit = {DEFAULT_THUMBNAIL_LIMIT};
  const video = document.getElementById("video");
  const timeEl = document.getElementById("time");
  const frameCountEl = document.getElementById("frameCount");
  const elapsedEl = document.getElementById("elapsed");
  const filmstrip = document.getElementById("filmstrip");
  const launchButton = document.getElementById("markLaunch");
  const hundredButton = document.getElementById("markHundred");
  const jumpLaunchButton = document.getElementById("jumpLaunch");
  const jumpHundredButton = document.getElementById("jumpHundred");
  const finishButton = document.getElementById("finish");
  const playPauseButton = document.getElementById("playPause");
  const viewModeButton = document.getElementById("viewMode");
  const resetViewButton = document.getElementById("resetView");
  const gaugeHint = document.getElementById("gaugeHint");
  const stage = document.querySelector(".stage");
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
  let launchIndex = null;
  let hundredIndex = null;
  const ARROW_HOLD_DELAY_MS = 350;
  let heldStep = 0;
  let heldKey = null;
  let holdTimer = null;
  const GAUGE_SCALE = 1.8;
  const DRAG_THRESHOLD = 4;
  let gaugeEnabled = false;
  let gaugePanX = 0;
  let gaugePanY = 0;
  let dragPointerId = null;
  let dragStartX = 0;
  let dragStartY = 0;
  let dragOriginX = 0;
  let dragOriginY = 0;
  let dragMoved = false;
  let suppressVideoClick = false;
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
    frameCountEl.textContent = String(selected + 1) + " / " + String(times.length);
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
    function finish(mediaTime) {{
      if (finished) return;
      finished = true;
      callback(typeof mediaTime === "number" ? mediaTime : video.currentTime);
    }}
    if (typeof video.requestVideoFrameCallback === "function") {{
      video.requestVideoFrameCallback(function (_now, metadata) {{
        finish(metadata.mediaTime);
      }});
      setTimeout(finish, 50);
    }} else {{
      requestAnimationFrame(finish);
    }}
  }}

  function pumpSeek() {{
    if (!times.length || seekInFlight || waitingForPaint) return;
    seekInFlight = true;
    // Nudge past the frame boundary so the browser lands inside the target
    // frame, but keep the offset under the gap to the next frame so high-fps
    // footage does not seek into the following frame.
    const i = requestedIndex;
    const gap = i + 1 < times.length ? times[i + 1] - times[i] : 0.04;
    video.currentTime = times[i] + Math.min(0.002, gap * 0.4);
  }}

  function requestIndex(index) {{
    if (!times.length) return;
    requestedIndex = Math.max(0, Math.min(times.length - 1, index));
    video.pause();
    pumpSeek();
  }}

  function stopHeldStep(key) {{
    if (key && heldKey !== key) return;
    if (holdTimer !== null) clearTimeout(holdTimer);
    holdTimer = null;
    heldKey = null;
    heldStep = 0;
  }}

  function beginArrowStep(key, step) {{
    stopHeldStep();
    heldKey = key;
    requestIndex(requestedIndex + step);
    holdTimer = setTimeout(function () {{
      holdTimer = null;
      heldStep = step;
      if (!seekInFlight && !waitingForPaint) requestIndex(requestedIndex + heldStep);
    }}, ARROW_HOLD_DELAY_MS);
  }}

  function finishSeek() {{
    const landed = nearestIndex(video.currentTime);
    const wasQueuedSeek = seekInFlight;
    seekInFlight = false;
    if (!wasQueuedSeek) requestedIndex = landed;
    waitingForPaint = true;
    afterVideoPaint(function (mediaTime) {{
      const painted = nearestIndex(mediaTime);
      showIndex(painted);
      waitingForPaint = false;
      if (painted !== requestedIndex) {{
        pumpSeek();
      }} else if (heldStep !== 0) {{
        requestedIndex = Math.max(0, Math.min(times.length - 1, painted + heldStep));
        if (requestedIndex !== painted) pumpSeek();
      }}
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

  function clampGaugePan() {{
    const range = (GAUGE_SCALE - 1) / 2;
    const maxX = stage.clientWidth * range;
    const maxY = stage.clientHeight * range;
    gaugePanX = Math.max(-maxX, Math.min(maxX, gaugePanX));
    gaugePanY = Math.max(-maxY, Math.min(maxY, gaugePanY));
  }}

  function applyGaugeView() {{
    clampGaugePan();
    video.style.transform = gaugeEnabled
      ? "translate3d(" + gaugePanX + "px," + gaugePanY + "px,0) scale(" + GAUGE_SCALE + ")"
      : "scale(1)";
  }}

  function resetGaugeView() {{
    gaugePanX = 0;
    gaugePanY = 0;
    applyGaugeView();
  }}

  function toggleViewMode() {{
    gaugeEnabled = !gaugeEnabled;
    stage.classList.toggle("gauge-view", gaugeEnabled);
    viewModeButton.textContent = gaugeEnabled ? "Fit video" : "Gauge view";
    viewModeButton.setAttribute("aria-pressed", String(gaugeEnabled));
    resetViewButton.hidden = !gaugeEnabled;
    gaugeHint.hidden = !gaugeEnabled;
    applyGaugeView();
  }}

  function startGaugeDrag(event) {{
    if (!gaugeEnabled || (event.pointerType === "mouse" && event.button !== 0)) return;
    dragPointerId = event.pointerId;
    dragStartX = event.clientX;
    dragStartY = event.clientY;
    dragOriginX = gaugePanX;
    dragOriginY = gaugePanY;
    dragMoved = false;
    video.setPointerCapture(event.pointerId);
  }}

  function moveGaugeDrag(event) {{
    if (event.pointerId !== dragPointerId) return;
    const deltaX = event.clientX - dragStartX;
    const deltaY = event.clientY - dragStartY;
    if (!dragMoved && Math.hypot(deltaX, deltaY) < DRAG_THRESHOLD) return;
    dragMoved = true;
    stage.classList.add("dragging");
    gaugePanX = dragOriginX + deltaX;
    gaugePanY = dragOriginY + deltaY;
    applyGaugeView();
    event.preventDefault();
  }}

  function finishGaugeDrag(event, cancelled) {{
    if (event.pointerId !== dragPointerId) return;
    if (video.hasPointerCapture(event.pointerId)) video.releasePointerCapture(event.pointerId);
    dragPointerId = null;
    stage.classList.remove("dragging");
    if (dragMoved && !cancelled) {{
      suppressVideoClick = true;
      setTimeout(function () {{ suppressVideoClick = false; }}, 0);
    }}
    dragMoved = false;
  }}

  function updateFinish() {{
    const complete = launch !== null && hundred !== null;
    const ordered = complete && hundred > launch;
    finishButton.disabled = !ordered;
    elapsedEl.textContent = complete ? (hundred - launch).toFixed(3) + " s" : "--.---";
    statusEl.textContent = complete && !ordered ? "The 100 km/h frame must come after launch." : "";
  }}

  function mark(button, which) {{
    if (!times.length) return;
    const value = times[selected];
    if (which === "launch") {{
      launch = value;
      launchIndex = selected;
      jumpLaunchButton.disabled = false;
    }} else {{
      hundred = value;
      hundredIndex = selected;
      jumpHundredButton.disabled = false;
    }}
    button.classList.add("marked");
    button.querySelector(".mark-value").textContent = value.toFixed(3) + " s";
    document.querySelectorAll("#filmstrip ." + which + "-mark").forEach(function (thumbnail) {{
      thumbnail.classList.remove(which + "-mark");
    }});
    const markedThumbnail = document.querySelector('[data-frame="' +
      String(Math.min(times.length - 1, Math.round(selected / thumbnailStep) * thumbnailStep)) + '"]');
    if (markedThumbnail) markedThumbnail.classList.add(which + "-mark");
    updateFinish();
  }}

  launchButton.addEventListener("click", function () {{ mark(launchButton, "launch"); }});
  hundredButton.addEventListener("click", function () {{ mark(hundredButton, "hundred"); }});
  jumpLaunchButton.addEventListener("click", function () {{ requestIndex(launchIndex); }});
  jumpHundredButton.addEventListener("click", function () {{ requestIndex(hundredIndex); }});
  document.getElementById("stepBackTen").addEventListener("click", function () {{ requestIndex(requestedIndex - 10); }});
  document.getElementById("stepBack").addEventListener("click", function () {{ requestIndex(requestedIndex - 1); }});
  playPauseButton.addEventListener("click", togglePlayback);
  viewModeButton.addEventListener("click", toggleViewMode);
  resetViewButton.addEventListener("click", resetGaugeView);
  document.getElementById("stepForward").addEventListener("click", function () {{ requestIndex(requestedIndex + 1); }});
  document.getElementById("stepForwardTen").addEventListener("click", function () {{ requestIndex(requestedIndex + 10); }});
  video.addEventListener("pointerdown", startGaugeDrag);
  video.addEventListener("pointermove", moveGaugeDrag);
  video.addEventListener("pointerup", function (event) {{ finishGaugeDrag(event, false); }});
  video.addEventListener("pointercancel", function (event) {{ finishGaugeDrag(event, true); }});
  video.addEventListener("click", function (event) {{
    if (suppressVideoClick) {{
      event.preventDefault();
      suppressVideoClick = false;
      return;
    }}
    togglePlayback();
  }});
  video.addEventListener("play", function () {{ playPauseButton.textContent = "Pause"; }});
  video.addEventListener("pause", function () {{ playPauseButton.textContent = "Play"; }});
  video.addEventListener("seeking", function () {{
    if (!seekInFlight) requestedIndex = nearestIndex(video.currentTime);
  }});
  video.addEventListener("seeked", finishSeek);
  video.addEventListener("timeupdate", syncPlayback);

  document.addEventListener("keydown", function (event) {{
    let destination = null;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {{
      event.preventDefault();
      if (event.repeat) return;
      const step = (event.key === "ArrowLeft" ? -1 : 1) * (event.shiftKey ? 10 : 1);
      beginArrowStep(event.key, step);
      return;
    }}
    if (event.key === "Home") destination = 0;
    else if (event.key === "End") destination = times.length - 1;
    else if (event.code === "Space") {{
      if (event.target.closest("button")) return;
      event.preventDefault();
      togglePlayback();
      return;
    }} else if (event.key.toLowerCase() === "z") {{
      event.preventDefault();
      toggleViewMode();
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

  document.addEventListener("keyup", function (event) {{
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") stopHeldStep(event.key);
  }});
  window.addEventListener("blur", function () {{ stopHeldStep(); }});
  window.addEventListener("resize", function () {{
    if (gaugeEnabled) applyGaugeView();
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
      document.body.innerHTML = '<div class="done"><div class="done-copy"><strong>Frames saved</strong><span>You can return to the terminal.</span></div></div>';
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
      launchButton.disabled = false;
      hundredButton.disabled = false;
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

  function syncSelected() {{
    if (!times.length) return;
    selected = nearestIndex(video.currentTime);
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
    if (!times.length || points.length === 3 || video.seeking) return;
    video.pause();
    syncSelected();
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
  video.addEventListener("seeked", syncSelected);
  // Playback moves the video without seek events. Keep the selected frame
  // following it so a click after pausing stamps the frame on screen, not
  // the frame from before playback started.
  video.addEventListener("pause", syncSelected);
  video.addEventListener("timeupdate", function () {{
    if (video.paused) return;
    syncSelected();
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
        elif path == "/font":
            self._send_bytes(handler, 200, _MANROPE_FONT.read_bytes(), "font/ttf")
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
    print("  Extracting preview frames...")
    thumbnails = extract_thumbnails(path, toolchain, step, workdir)
    expected = len(thumbnail_indices(count))
    if len(thumbnails) != expected:
        print(
            f"Warning: expected {expected} thumbnails but ffmpeg produced "
            f"{len(thumbnails)}; picker times near the end of the clip may be slightly off.",
            file=sys.stderr,
        )

    duration = times[-1] if times else 0.0
    browser_video = prepare_browser_video(path, toolchain, workdir, duration=duration)
    server = _PickerServer(browser_video, workdir, times, video_name=path.name)
    server.start()
    try:
        print("  Opening the picker in your browser...")
        webbrowser.open(server.url)
        print("Waiting for launch and 100 km/h marks in the browser...")
        while not server.result_event.wait(0.1):
            pass
        if server.cancelled:
            raise KeyboardInterrupt
        if server.result is None:
            raise MediaError("frame picker closed without returning marks")
        return server.result
    finally:
        server.stop()
