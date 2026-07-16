# zero2hundred

Command line tool that turns dashboard footage into a finished 0-100 km/h
clip. Give it the launch and 100 km/h timestamps and it overlays a stopwatch
counting up in big MM:SS:cc digits (00:08:79 style), cuts the video at the
frame the speedometer hits 100, and holds that frame for two seconds so the
time stays on screen. Audio is kept and the source file is never touched.

Phone footage works as-is. Portrait videos keep their orientation, and the
timestamps you enter get snapped to real frames, so variable frame rate
recordings cut exactly where you picked.

## Requirements

- Python 3.11 or newer, from [python.org](https://www.python.org/downloads/) or the Microsoft Store
- FFmpeg (FFprobe comes with it)

If you don't have FFmpeg yet:

```powershell
winget install ffmpeg      # Windows
```

```bash
sudo apt install ffmpeg    # Debian/Ubuntu
brew install ffmpeg        # macOS
```

Open a new terminal afterwards and check that `ffmpeg -version` and
`python --version` both print something. If FFmpeg is missing the tool will
tell you at startup instead of failing halfway through.

## Install

```powershell
python -m pip install git+https://github.com/vroslmend/zero2hundred.git
```

That needs git on your machine. If you don't have git either, pip can install
straight from the zip:

```powershell
python -m pip install https://github.com/vroslmend/zero2hundred/archive/refs/heads/main.zip
```

Use `python3` instead of `python` on systems where that is the Python command.
If `zero2hundred` is not found after installation, you can always run the tool
through Python:

```powershell
python -m zero2hundred --help
```

## Usage

```powershell
zero2hundred "D:\Videos\run.mp4" --start 1.395 --end 10.193
```

On Windows, `zero2hundred.exe` runs the same command, but the `.exe` suffix is
not needed.

Run it with no arguments and it will ask for everything. On Windows you can
drag the video from Explorer straight into the terminal.

If you don't know the exact frames, use the picker:

```powershell
zero2hundred "D:\Videos\run.mp4" --pick
```

The picker runs only on your computer at `127.0.0.1`. The video and frame
timestamps are not uploaded anywhere. Some phone videos use a format that the
browser cannot play directly. In that case, the tool prepares a temporary
full-resolution browser-compatible copy before opening the picker.

Once the picker opens:

1. Play or pause the video to get close to the launch.
2. Use the arrow keys to step to the exact frame. Shift+arrows move ten frames.
3. Press L to mark the launch.
4. Play or step to the frame where the needle hits 100.
5. Press H to mark 100 km/h.
6. Press Finish.
7. Go back to the terminal. It renders automatically and saves
   `run_0-100.mp4` next to the original.

Closing the picker tab before pressing Finish cancels the run. You can also
press Ctrl+C in the terminal to cancel. The local server and temporary picker
files are cleaned up in both cases.

To inspect the FFmpeg command without creating an output video, add
`--dry-run`:

```powershell
zero2hundred "D:\Videos\run.mp4" --pick --dry-run
```

Your times get snapped to exact frames automatically, so being a fraction of
a second off when typing is fine.

All options:

```text
input                  Video to process
--start TIME           Launch timestamp
--end TIME             100 km/h timestamp
--pick                 Mark exact frames in the browser
-o, --output PATH      Where to save the result
--freeze SECONDS       How long the final frame holds
--position POSITION    top-left, top-center, top-right,
                       bottom-left, bottom-center, bottom-right
--font NAME            Timer font family
--font-file PATH       Timer font file
--trim-intro           Cut everything before the launch
--config PATH          Load defaults from a TOML file
--overwrite            Replace an existing output file
--dry-run              Print the FFmpeg command instead of running it
--version              Print the installed version
-h, --help             Show command help
```

Times can be written as `4.267`, `00:04.267`, or `00:00:04.267`.

## Configuration

Anything you pass on the command line wins over the TOML file:

```toml
freeze_duration = 2.0
position = "bottom-center"
timer_style = "stopwatch"   # "hms" gives HH:MM:SS.mmm instead
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

## A note on the numbers

The timer stops when the speedometer shows 100, and speedometers read a few
percent high from the factory. So the clip flatters your car a little. If you
want real numbers, get a GPS box like a Dragy. This is for the video.

## Tests

From a cloned repository, install it in editable mode once and then run the
test suite:

```powershell
python -m pip install -e .
python -m unittest discover -s tests
```
