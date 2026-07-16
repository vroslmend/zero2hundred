# zero2hundred

I used to edit my 0-100 km/h runs by hand: drop a stopwatch overlay on the
clip, then cut it at the exact frame the needle touches 100. This tool does
that edit in one command.

Point it at your dashboard footage and give it two timestamps, launch and
100 km/h. It exports a finished clip with a stopwatch counting up from launch
in big MM:SS:cc digits at the bottom (00:08:79 style). At 100 the video stops
and holds the final frame for two seconds, so the time sits on screen. Audio
stays. The source file is never touched.

Phone footage works as-is. Portrait videos keep their orientation, and because
phone cameras record with a variable frame rate, the times you type get
snapped to actual frames. The cut lands exactly where you picked it.

## Requirements

- Python 3.11 or newer
- FFmpeg and FFprobe on `PATH`

## Install

```powershell
python -m pip install -e .
```

## Usage

```powershell
zero2hundred "D:\Videos\run.mp4" --start 1.395 --end 10.193
```

Run it with no arguments and it will ask for everything. On Windows you can
drag the video from Explorer straight into the terminal.

If you don't know the exact frames, use the picker:

```powershell
zero2hundred "D:\Videos\run.mp4" --pick
```

It opens a page in your browser with every frame of the video. Arrow keys move
one frame, Shift+arrows move ten. Step to the launch frame, copy its
timestamp, do the same for the 100 km/h frame, then paste both back in the
terminal.

The output lands next to the input as `run_0-100.mp4`.

All options:

```text
--pick                 Browse frames in the browser to find exact times
--output PATH          Where to save the result
--freeze SECONDS       How long the final frame holds
--position POSITION    top-left, top-center, top-right,
                       bottom-left, bottom-center, bottom-right
--font NAME            Timer font family
--font-file PATH       Timer font file
--trim-intro           Cut everything before the launch
--config PATH          Load defaults from a TOML file
--overwrite            Replace an existing output file
--dry-run              Print the FFmpeg command instead of running it
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

```powershell
python -m unittest discover -s tests
```
