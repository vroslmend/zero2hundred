# zero2hundred

`zero2hundred` creates a finished 0–100 km/h video from dashboard footage. The
current CLI handles the timer overlay, cut at 100 km/h, two-second freeze frame,
audio padding, and export. It accepts timestamps as seconds or timecodes.

## Requirements

- Python 3.11 or newer
- FFmpeg and FFprobe available on `PATH`

## Install

```powershell
python -m pip install -e .
```

## Use

Pass a video and the launch and 100 km/h timestamps:

```powershell
zero2hundred "D:\Videos\run.mp4" --start 4.267 --end 10.833
```

Run without arguments for an interactive prompt. On Windows, a video can be
dragged from File Explorer into the terminal when the input prompt is visible.

```powershell
zero2hundred
```

By default, the result is saved beside the input as `run_0-100.mp4`. The source
is never overwritten.

Useful options:

```text
--output PATH          Choose the output path
--freeze SECONDS       Change the final freeze duration
--position POSITION    top-left, top-right, bottom-left, or bottom-right
--trim-intro           Remove footage before the launch
--config PATH          Load rendering defaults from a TOML file
--overwrite            Replace an existing output file
--dry-run              Print the FFmpeg command without executing it
```

Time values may be written as `4.267`, `00:04.267`, or `00:00:04.267`.

## Configuration

Command-line options take precedence over a TOML configuration file:

```toml
freeze_duration = 2.0
position = "bottom-right"
font = "Arial"
font_size_ratio = 0.065
margin_ratio = 0.04
text_color = "white"
border_color = "black"
border_width = 4
video_encoder = "libx264"
crf = 18
preset = "medium"
audio_bitrate = "192k"
```

## Tests

```powershell
python -m unittest discover -s tests
```

