# zero2hundred

`zero2hundred` turns dashboard footage into a finished 0–100 km/h video: a large
stopwatch overlay counts from the launch, the clip cuts at the moment 100 km/h
is reached, the final frame freezes for two seconds with the elapsed time on
screen, and the result is exported next to the source. The source video is
never modified.

The timer renders as `MM:SS:cc` (for example `00:08:79`) centered at the bottom
of the frame, white with a black border. Rotated phone footage is handled
automatically, and the timestamps you enter are snapped to real video frames,
so variable-frame-rate phone recordings stay accurate.

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
zero2hundred "D:\Videos\run.mp4" --start 1.395 --end 10.193
```

Run without arguments for an interactive prompt. On Windows, a video can be
dragged from File Explorer into the terminal when the input prompt is visible.

To find the exact frames, add `--pick`: a frame picker opens in your browser
where you can step frame by frame (arrow keys, Shift for ×10), see each frame's
exact timestamp, and copy it. Enter the copied launch and 100 km/h times back
in the terminal.

```powershell
zero2hundred "D:\Videos\run.mp4" --pick
```

By default, the result is saved beside the input as `run_0-100.mp4`.

Useful options:

```text
--pick                 Open a frame picker in the browser to find exact times
--output PATH          Choose the output path
--freeze SECONDS       Change the final freeze duration
--position POSITION    top-left, top-center, top-right,
                       bottom-left, bottom-center, or bottom-right
--font NAME            Timer font family
--font-file PATH       Timer font file
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
position = "bottom-center"
timer_style = "stopwatch"   # or "hms" for HH:MM:SS.mmm
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

## Accuracy

The measurement is only as truthful as the speedometer on screen. Car
speedometers are required to read at or above the true speed and commonly
over-read by several percent, so an indicated 100 km/h arrives a little early.
Treat the result as a nice clip, not as instrumentation — GPS-based timing
hardware is the right tool for real numbers.

## Tests

```powershell
python -m unittest discover -s tests
```
