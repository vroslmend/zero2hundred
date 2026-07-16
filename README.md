# zero2hundred

zero2hundred helps you mark the exact launch and 100 km/h frames in dashboard
footage, then creates a finished timed video automatically.

The frame picker runs in your browser and stays on your computer. It provides
full-resolution playback, exact frame stepping, a movable gauge view, and a live
elapsed-time result. The source video is never modified.

You choose the launch and 100 km/h frames yourself. The tool does not guess
them automatically.

## Requirements

- Python 3.11 or newer
- FFmpeg and FFprobe
- A modern web browser

There are no additional Python dependencies in the standard installation.

### Windows

Install Python from [python.org](https://www.python.org/downloads/) or the
Microsoft Store. During a python.org installation, enable the option that adds
Python to `PATH`.

Install FFmpeg from PowerShell:

```powershell
winget install --id Gyan.FFmpeg --exact
```

Install [pipx](https://pipx.pypa.io/), which keeps command-line applications in
their own Python environments:

```powershell
python -m pip install --user pipx
python -m pipx ensurepath
```

### macOS

If you use [Homebrew](https://brew.sh/):

```bash
brew install python ffmpeg pipx
pipx ensurepath
```

### Debian 12 or newer and Ubuntu 24.04 or newer

```bash
sudo apt update
sudo apt install python3 python3-venv ffmpeg pipx
pipx ensurepath
```

Check that `python3 --version` reports Python 3.11 or newer. If it does not,
install a supported Python using the method recommended by your distribution
before continuing.

After installing the prerequisites above, close this terminal and open a new one
so the updated `PATH` takes effect.

## Install zero2hundred

The installation does not require Git:

```bash
pipx install https://github.com/vroslmend/zero2hundred/archive/refs/heads/main.zip
```

Verify the installation before processing a video.

Windows:

```powershell
python --version
ffmpeg -version
ffprobe -version
zero2hundred --version
```

macOS and Linux:

```bash
python3 --version
ffmpeg -version
ffprobe -version
zero2hundred --version
```

## Quick start

Open PowerShell or a terminal and run:

```powershell
zero2hundred "D:\Videos\run.mp4" --pick
```

Paths containing spaces must stay inside quotation marks.

The program inspects the video, reads its exact frame timestamps, prepares the
picker, and opens it in your default browser. Some phone videos cannot play
directly in a browser. When needed, the program creates a temporary,
full-resolution H.264 preview first. This can take a little time and does not
change the source video.

### Pick the frames

1. Play the video to get close to the launch.
2. Tap the left or right arrow key to move one frame at a time.
3. Press `L` on the exact launch frame.
4. Find the exact frame where the speedometer reaches 100 km/h.
5. Press `H` to mark it.
6. Review the elapsed time and jump back to either mark if needed.
7. Press **Use these frames**.
8. Return to the terminal. The video renders automatically.

Useful picker controls:

| Control | Action |
|---|---|
| `Space` | Play or pause |
| `Left` / `Right` | Move one frame |
| Hold `Left` / `Right` | Inspect consecutive frames |
| `Shift` + arrow | Move ten frames |
| `Home` / `End` | Jump to the first or last frame |
| `L` | Mark launch |
| `H` | Mark 100 km/h |
| `Z` | Turn Gauge view on or off |

Gauge view enlarges the video around the instrument cluster. While it is on,
drag the video to position the speedometer. Select **Reset view** to restore the
default position.

## What the output contains

By default:

1. The output starts at the beginning of the source video.
2. The timer stays at zero until the marked launch frame.
3. The timer runs until the marked 100 km/h frame.
4. That frame and the final time hold for two seconds.
5. The timer disappears and the original video continues to the end.

Source audio is retained when present. The default result is saved beside the
source as `run_0-100.mp4`.

If that filename already exists, the program chooses the next available name,
such as `run_0-100_2.mp4`. Use `--overwrite` only when you intentionally want to
replace an existing result. The source video itself cannot be overwritten.

Common output changes:

```powershell
# Start the output at launch
zero2hundred "D:\Videos\run.mp4" --pick --trim-intro

# Hold the final result for three seconds
zero2hundred "D:\Videos\run.mp4" --pick --freeze 3

# End after the frozen result instead of continuing the video
zero2hundred "D:\Videos\run.mp4" --pick --end-after-freeze

# Choose an output path
zero2hundred "D:\Videos\run.mp4" --pick --output "D:\Videos\finished.mp4"
```

## Enter timestamps manually

The browser picker is recommended when you need exact visual timing. If you
already know both timestamps, supply them directly:

```powershell
zero2hundred "D:\Videos\run.mp4" --start 1.395 --end 10.982
```

Times can be written as seconds, `MM:SS`, or `HH:MM:SS`:

```text
4.267
00:04.267
00:00:04.267
```

Typed timestamps are snapped to real source frames. This also keeps timing
accurate for variable-frame-rate phone recordings.

If only `--start` or only `--end` is supplied, the terminal asks for the missing
timestamp. Running `zero2hundred` without any arguments asks for the video path
and both timestamps. `--pick` cannot be combined with `--start` or `--end`.

## Command options

Run `zero2hundred --help` for the authoritative option list.

| Option | Purpose |
|---|---|
| `VIDEO` | Video to process |
| `--pick` | Mark both exact frames in the browser |
| `--start TIME` | Set the launch timestamp manually |
| `--end TIME` | Set the 100 km/h timestamp manually |
| `-o PATH`, `--output PATH` | Choose the output MP4 path |
| `--freeze SECONDS` | Set the frozen-result duration |
| `--trim-intro` | Remove footage before launch |
| `--end-after-freeze` | End on the frozen result |
| `--continue-after-freeze` | Continue after the freeze, overriding a config file |
| `--position POSITION` | Position the timer at one of six screen locations |
| `--font NAME` | Use an installed timer font family |
| `--font-file PATH` | Use a specific timer font file |
| `--config PATH` | Load settings from a TOML file |
| `--overwrite` | Replace an existing output file |
| `--dry-run` | Show the FFmpeg command without exporting |
| `--version` | Show the installed version |
| `-h`, `--help` | Show command help |

The six timer positions are `top-left`, `top-center`, `top-right`,
`bottom-left`, `bottom-center`, and `bottom-right`.

`--dry-run` still inspects the video and collects timing. It stops after showing
the FFmpeg command and never creates or replaces an output file.

## Configuration

You can skip this section unless you want reusable render and overlay settings.

Create a file such as `zero2hundred.toml`:

```toml
freeze_duration = 2.0
continue_after_freeze = true
position = "bottom-center"
overlay_style = "type-only"
timer_format = "seconds"
font = "Manrope"
```

Use it with `--config`:

```powershell
zero2hundred "D:\Videos\run.mp4" --pick --config "D:\Videos\zero2hundred.toml"
```

Values supplied directly on the command line override matching values from the
configuration file.

### Overlay settings

| Setting | Default | Allowed values |
|---|---:|---|
| `freeze_duration` | `2.0` | Zero or more seconds |
| `continue_after_freeze` | `true` | `true` or `false` |
| `position` | `"bottom-center"` | Any of the six timer positions |
| `overlay_style` | `"type-only"` | `"type-only"`, `"quiet-plate"`, or `"compact"` |
| `bottom_clearance_ratio` | `0.16` | `0.0` to `0.5` |
| `overlay_scale` | `1.0` | `0.5` to `2.0` |
| `timer_format` | `"seconds"` | `"seconds"` or `"stopwatch"` |
| `timer_label` | `"0–100 km/h"` | Any nonempty text |
| `font` | `"Manrope"` | An installed font family |
| `font_file` | Not set | Path to a font file |
| `font_size_ratio` | `0.065` | `0.01` to `0.5` |
| `margin_ratio` | `0.04` | `0.0` to `0.5` |
| `text_color` | `"white"` | An FFmpeg color value |
| `border_color` | `"black"` | An FFmpeg color value |
| `border_width` | `1` | Zero or a positive integer |
| `panel_color` | `"black@0.58"` | An FFmpeg color value |
| `accent_color` | `"white@0.22"` | An FFmpeg color value |

The default `type-only` overlay uses clean typography without a panel.
`quiet-plate` adds a subtle dark background, and `compact` puts the label and
timer on one line. `seconds` displays a value such as `9.45`, while `stopwatch`
uses a minutes, seconds, and centiseconds display.

Manrope Medium is bundled with the tool, so the default appearance is
consistent across computers.

### Encoding settings

These normally do not need to change:

```toml
video_encoder = "libx264"
crf = 18
preset = "medium"
audio_bitrate = "192k"
```

## Troubleshooting

### `zero2hundred` is not recognized

Open a new terminal after installation. If the command is still unavailable,
run:

```powershell
pipx ensurepath
pipx list
```

Open another terminal after `pipx ensurepath`. The `pipx list` output should
show `zero2hundred` and its command.

### FFmpeg or FFprobe was not found

Open a new terminal and check both commands:

```powershell
ffmpeg -version
ffprobe -version
```

If either command is missing, repeat the FFmpeg installation for your operating
system.

### The browser did not open

Make sure your operating system has a default browser, then press `Ctrl+C` to
cancel and run the command again. The terminal prints `Cancelled.` and removes
temporary picker files.

### The picker is preparing a browser-compatible preview

This is expected for formats such as HEVC or for videos whose rotation must be
applied before browser playback. The preview remains full resolution and is
deleted when the picker closes.

### An output file already exists

Without an explicit `--output`, the program normally chooses a numbered
filename automatically. When using an explicit output path, choose another path
or add `--overwrite` if replacing that file is intentional.

### Cancel a run

Close the picker tab before selecting **Use these frames**, or press `Ctrl+C` in
the terminal. The local picker server and its temporary files are cleaned up.

## Privacy

The picker is served from `127.0.0.1`, which is accessible only from your
computer. Video data, thumbnails, timestamps, and marks are not uploaded.

## Update or uninstall

Update to the latest version from the repository:

```powershell
pipx reinstall zero2hundred
```

Uninstall:

```powershell
pipx uninstall zero2hundred
```

## A note on the result

The timer stops when the dashboard speedometer shows 100 km/h. Factory
speedometers commonly read slightly higher than the car's true road speed, so
this is a video-based result rather than a GPS performance measurement.

## Development

Clone the repository and install it in editable mode:

```powershell
git clone https://github.com/vroslmend/zero2hundred.git
cd zero2hundred
python -m pip install -e .
```

Run the complete test suite:

```powershell
python -m unittest discover -s tests
```

## License

zero2hundred is available under the [MIT License](LICENSE).

---

Made by [Ammar Hassan](https://github.com/vroslmend).
