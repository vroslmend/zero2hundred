from __future__ import annotations

import html
import json
import math
from pathlib import Path
import subprocess
import sys

from zero2hundred.errors import MediaError
from zero2hundred.media import Toolchain

DEFAULT_THUMBNAIL_LIMIT = 1200


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
        video_filter = "scale=-2:220"
    else:
        video_filter = rf"select='not(mod(n\,{step}))',scale=-2:220"

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


def write_picker_html(
    entries: list[tuple[float, str]], workdir: Path, video_name: str
) -> Path:
    """Write a self-contained frame-picker HTML page and return its path."""
    frames = [[f"{pts:.3f}", relative_path] for pts, relative_path in entries]
    frames_json = json.dumps(frames)
    safe_name = html.escape(video_name)

    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Frame picker - {safe_name}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: #111318;
    color: #e8e8ec;
    font-family: -apple-system, Segoe UI, Arial, sans-serif;
  }}
  header {{
    padding: 16px 20px;
    border-bottom: 1px solid #2a2d36;
  }}
  header h1 {{
    margin: 0 0 4px;
    font-size: 18px;
  }}
  header p {{
    margin: 0;
    color: #9aa0ac;
    font-size: 13px;
  }}
  main {{
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 20px;
    gap: 12px;
  }}
  #preview {{
    max-width: 100%;
    max-height: 60vh;
    background: #000;
    border: 1px solid #2a2d36;
  }}
  #time {{
    font-family: "Consolas", "SFMono-Regular", Menlo, monospace;
    font-size: 64px;
    font-weight: bold;
    letter-spacing: 1px;
  }}
  #copyBtn {{
    background: #3b6ef6;
    color: #fff;
    border: none;
    border-radius: 6px;
    padding: 10px 22px;
    font-size: 15px;
    cursor: pointer;
  }}
  #copyBtn:active {{
    background: #2f57c4;
  }}
  #copyStatus {{
    color: #7fd08a;
    font-size: 13px;
    min-height: 16px;
  }}
  #filmstrip {{
    width: 100%;
    display: flex;
    overflow-x: auto;
    gap: 4px;
    padding: 8px;
    background: #181b21;
    border-top: 1px solid #2a2d36;
  }}
  #filmstrip img {{
    height: 90px;
    flex: 0 0 auto;
    cursor: pointer;
    opacity: 0.6;
    border: 2px solid transparent;
  }}
  #filmstrip img.selected {{
    opacity: 1;
    border-color: #3b6ef6;
  }}
</style>
</head>
<body>
<header>
  <h1>{safe_name}</h1>
  <p>Step to the launch frame and the 100 km/h frame, copy each time, then enter them in the terminal.</p>
</header>
<main>
  <img id="preview" src="" alt="selected frame">
  <div id="time">0.000</div>
  <button id="copyBtn" type="button">Copy time</button>
  <div id="copyStatus"></div>
</main>
<div id="filmstrip"></div>
<script>
  var frames = {frames_json};
  var selected = 0;
  var filmstrip = document.getElementById("filmstrip");
  var preview = document.getElementById("preview");
  var timeEl = document.getElementById("time");
  var copyStatus = document.getElementById("copyStatus");

  frames.forEach(function (frame, index) {{
    var img = document.createElement("img");
    img.src = frame[1];
    img.setAttribute("loading", "lazy");
    img.id = "thumb-" + index;
    img.alt = "frame " + index;
    img.addEventListener("click", function () {{
      select(index);
    }});
    filmstrip.appendChild(img);
  }});

  function render() {{
    var frame = frames[selected];
    preview.src = frame[1];
    timeEl.textContent = frame[0];
    var thumbs = filmstrip.querySelectorAll("img");
    for (var i = 0; i < thumbs.length; i++) {{
      thumbs[i].classList.remove("selected");
    }}
    var current = document.getElementById("thumb-" + selected);
    if (current) {{
      current.classList.add("selected");
      current.scrollIntoView({{behavior: "smooth", inline: "center", block: "nearest"}});
    }}
    copyStatus.textContent = "";
  }}

  function select(index) {{
    if (index < 0) index = 0;
    if (index > frames.length - 1) index = frames.length - 1;
    selected = index;
    render();
  }}

  document.addEventListener("keydown", function (event) {{
    var delta = 0;
    if (event.key === "ArrowLeft") {{
      delta = event.shiftKey ? -10 : -1;
    }} else if (event.key === "ArrowRight") {{
      delta = event.shiftKey ? 10 : 1;
    }} else if (event.key === "Home") {{
      event.preventDefault();
      select(0);
      return;
    }} else if (event.key === "End") {{
      event.preventDefault();
      select(frames.length - 1);
      return;
    }} else {{
      return;
    }}
    event.preventDefault();
    select(selected + delta);
  }});

  function fallbackCopy(text) {{
    var textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    try {{
      document.execCommand("copy");
    }} catch (err) {{
      /* clipboard unavailable; nothing more we can do */
    }}
    document.body.removeChild(textarea);
  }}

  document.getElementById("copyBtn").addEventListener("click", function () {{
    var text = frames[selected][0];
    if (navigator.clipboard && navigator.clipboard.writeText) {{
      navigator.clipboard.writeText(text).catch(function () {{
        fallbackCopy(text);
      }});
    }} else {{
      fallbackCopy(text);
    }}
    copyStatus.textContent = "Copied " + text;
  }});

  if (frames.length) {{
    render();
  }}
</script>
</body>
</html>
"""
    html_path = workdir / "picker.html"
    html_path.write_text(document, encoding="utf-8")
    return html_path


def build_picker(
    path: Path, toolchain: Toolchain, times: list[float], workdir: Path
) -> Path:
    """Extract thumbnails for `times` and write the picker HTML, returning its path."""
    count = len(times)
    indices = thumbnail_indices(count)
    step = _step_for(count, DEFAULT_THUMBNAIL_LIMIT)
    thumbnails = extract_thumbnails(path, toolchain, step, workdir)

    if len(thumbnails) != len(indices):
        print(
            f"Warning: expected {len(indices)} thumbnails but ffmpeg produced "
            f"{len(thumbnails)}; picker times near the end of the clip may be slightly off.",
            file=sys.stderr,
        )

    paired = min(len(indices), len(thumbnails))
    entries = [
        (times[indices[i]], thumbnails[i].relative_to(workdir).as_posix())
        for i in range(paired)
    ]
    return write_picker_html(entries, workdir, path.name)
