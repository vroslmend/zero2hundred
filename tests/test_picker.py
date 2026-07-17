import contextlib
import http.client
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from zero2hundred.errors import MediaError
from zero2hundred.detect.needle import Calibration
from zero2hundred.media import Toolchain
from zero2hundred.picker import (
    _CalibrationServer,
    _PickerServer,
    extract_thumbnails,
    prepare_browser_video,
    render_calibration_html,
    render_picker_html,
    serve_calibration,
    serve_picker,
    thumbnail_indices,
)


class ThumbnailIndicesTests(unittest.TestCase):
    def test_count_under_limit_returns_every_index(self) -> None:
        self.assertEqual(thumbnail_indices(5, limit=10), [0, 1, 2, 3, 4])

    def test_count_equal_to_limit_returns_every_index(self) -> None:
        self.assertEqual(thumbnail_indices(10, limit=10), list(range(10)))

    def test_zero_count_returns_empty_list(self) -> None:
        self.assertEqual(thumbnail_indices(0, limit=10), [])

    def test_over_limit_uses_step_and_includes_zero(self) -> None:
        indices = thumbnail_indices(25, limit=10)
        self.assertEqual(indices[0], 0)
        self.assertEqual(indices, sorted(set(indices)))
        self.assertTrue(all(0 <= i < 25 for i in indices))
        self.assertEqual(indices, [0, 3, 6, 9, 12, 15, 18, 21, 24])

    def test_over_limit_result_is_sorted_and_unique(self) -> None:
        indices = thumbnail_indices(4731, limit=1200)
        self.assertEqual(indices, sorted(set(indices)))
        self.assertLessEqual(len(indices), 1200)
        self.assertEqual(indices[0], 0)
        self.assertTrue(all(0 <= i < 4731 for i in indices))


class ExtractThumbnailsTests(unittest.TestCase):
    def test_uses_160_pixel_height_and_preserves_passthrough_flags(self) -> None:
        toolchain = Toolchain(ffmpeg="ffmpeg", ffprobe="ffprobe")

        def fake_run(command, **kwargs):
            pattern = Path(command[-1])
            pattern.parent.mkdir(parents=True, exist_ok=True)
            (pattern.parent / "000001.jpg").write_bytes(b"x")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("zero2hundred.picker.subprocess.run", side_effect=fake_run) as run:
                extract_thumbnails(Path("input.mp4"), toolchain, 3, Path(tmp))

        command = run.call_args.args[0]
        self.assertIn("select='not(mod(n\\,3))',scale=-2:160", command)
        self.assertIn("-fps_mode", command)
        self.assertIn("passthrough", command)

    def test_falls_back_to_vsync_zero(self) -> None:
        toolchain = Toolchain(ffmpeg="ffmpeg", ffprobe="ffprobe")
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            if len(calls) == 1:
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="failed")
            pattern = Path(command[-1])
            pattern.parent.mkdir(parents=True, exist_ok=True)
            (pattern.parent / "000001.jpg").write_bytes(b"x")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("zero2hundred.picker.subprocess.run", side_effect=fake_run):
                extract_thumbnails(Path("input.mp4"), toolchain, 1, Path(tmp))

        self.assertIn("-fps_mode", calls[0])
        self.assertIn("passthrough", calls[0])
        self.assertIn("-vsync", calls[1])
        self.assertIn("0", calls[1])


class PrepareBrowserVideoTests(unittest.TestCase):
    def test_keeps_browser_safe_h264_mp4_unchanged(self) -> None:
        toolchain = Toolchain(ffmpeg="ffmpeg", ffprobe="ffprobe")
        probe = subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps(
                {"streams": [{"codec_name": "h264", "pix_fmt": "yuv420p"}]}
            ),
            stderr="",
        )

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "input.mp4"
            source.write_bytes(b"video")
            with mock.patch(
                "zero2hundred.picker.subprocess.run", return_value=probe
            ) as run:
                result = prepare_browser_video(source, toolchain, Path(tmp))

        self.assertEqual(result, source)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][0], "ffprobe")

    def test_transcodes_hevc_10_bit_with_passthrough_timing(self) -> None:
        toolchain = Toolchain(ffmpeg="ffmpeg", ffprobe="ffprobe")
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            if command[0] == "ffprobe":
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps(
                        {
                            "streams": [
                                {"codec_name": "hevc", "pix_fmt": "yuv420p10le"}
                            ]
                        }
                    ),
                    stderr="",
                )
            Path(command[-1]).write_bytes(b"preview")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input.mp4"
            source.write_bytes(b"video")
            with mock.patch("zero2hundred.picker.subprocess.run", side_effect=fake_run):
                with contextlib.redirect_stdout(io.StringIO()):
                    result = prepare_browser_video(source, toolchain, root)

            self.assertEqual(result, root / "browser-preview.mp4")
            self.assertEqual(result.read_bytes(), b"preview")

        command = commands[1]
        self.assertEqual(command[0], "ffmpeg")
        self.assertIn("libx264", command)
        self.assertIn("yuv420p", command)
        self.assertIn("-fps_mode", command)
        self.assertIn("passthrough", command)
        self.assertIn("-enc_time_base", command)
        self.assertIn("demux", command)
        self.assertIn("-g", command)
        self.assertIn("12", command)
        self.assertIn("+faststart", command)

    def test_transcodes_rotated_h264_so_browser_dimensions_match(self) -> None:
        toolchain = Toolchain(ffmpeg="ffmpeg", ffprobe="ffprobe")
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            if command[0] == "ffprobe":
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps(
                        {
                            "streams": [
                                {
                                    "codec_name": "h264",
                                    "pix_fmt": "yuv420p",
                                    "side_data_list": [{"rotation": -90}],
                                }
                            ]
                        }
                    ),
                    stderr="",
                )
            Path(command[-1]).write_bytes(b"upright preview")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input.mp4"
            source.write_bytes(b"video")
            with mock.patch("zero2hundred.picker.subprocess.run", side_effect=fake_run):
                with contextlib.redirect_stdout(io.StringIO()):
                    result = prepare_browser_video(source, toolchain, root)

        self.assertEqual(result.name, "browser-preview.mp4")
        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[1][0], "ffmpeg")

    def test_reports_progress_when_duration_is_known(self) -> None:
        toolchain = Toolchain(ffmpeg="ffmpeg", ffprobe="ffprobe")
        probe = subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps(
                {"streams": [{"codec_name": "hevc", "pix_fmt": "yuv420p10le"}]}
            ),
            stderr="",
        )

        class FakePopen:
            def __init__(self, command, **kwargs):
                Path(command[-1]).write_bytes(b"preview")
                self.stdout = iter(
                    ["out_time_us=5000000\n", "out_time_us=10000000\n", "progress=end\n"]
                )

            def wait(self):
                return 0

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input.mp4"
            source.write_bytes(b"video")
            stdout = io.StringIO()
            with mock.patch("zero2hundred.picker.subprocess.run", return_value=probe):
                with mock.patch("zero2hundred.progress.subprocess.Popen", FakePopen):
                    with contextlib.redirect_stdout(stdout):
                        result = prepare_browser_video(
                            source, toolchain, root, duration=10.0
                        )

        self.assertEqual(result, root / "browser-preview.mp4")
        self.assertIn("Progress", stdout.getvalue())
        self.assertIn("100%", stdout.getvalue())

    def test_reports_preview_transcode_failure(self) -> None:
        toolchain = Toolchain(ffmpeg="ffmpeg", ffprobe="ffprobe")
        probe = subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps(
                {"streams": [{"codec_name": "hevc", "pix_fmt": "yuv420p10le"}]}
            ),
            stderr="",
        )
        failure = subprocess.CompletedProcess([], 1, stdout="", stderr="encode failed")

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "input.mp4"
            source.write_bytes(b"video")
            with mock.patch(
                "zero2hundred.picker.subprocess.run", side_effect=[probe, failure]
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaisesRegex(
                        MediaError,
                        "could not create a browser-compatible preview: encode failed",
                    ):
                        prepare_browser_video(source, toolchain, Path(tmp))


class RenderPickerHtmlTests(unittest.TestCase):
    def test_renders_video_times_marks_and_no_external_urls(self) -> None:
        text = render_picker_html("sample_video.mp4")

        self.assertIn("sample_video.mp4", text)
        self.assertIn('<video id="video" src="/video" preload="metadata" playsinline', text)
        self.assertNotIn('<video id="video" src="/video" controls', text)
        self.assertIn('fetch("/times")', text)
        self.assertIn("Mark launch", text)
        self.assertIn("Mark 100 km/h", text)
        self.assertIn("Finish", text)
        self.assertIn("requestVideoFrameCallback", text)
        self.assertIn("setTimeout(finish, 50)", text)
        self.assertIn("seekInFlight", text)
        self.assertIn("requestedIndex", text)
        self.assertIn('id="frameCount"', text)
        self.assertIn("Current frame", text)
        self.assertIn('class="shortcut-guide"', text)
        self.assertIn("Keyboard shortcuts", text)
        self.assertIn("Mark a frame again to replace it.", text)
        self.assertIn('id="elapsed"', text)
        self.assertIn('id="stepBackTen"', text)
        self.assertIn('id="stepBack"', text)
        self.assertIn('id="playPause"', text)
        self.assertIn('id="stepForward"', text)
        self.assertIn('id="stepForwardTen"', text)
        self.assertIn('id="viewMode"', text)
        self.assertIn('id="resetView"', text)
        self.assertIn('id="gaugeHint"', text)
        self.assertIn("object-fit: contain", text)
        self.assertIn("gauge-view", text)
        self.assertIn("const GAUGE_SCALE = 1.8", text)
        self.assertIn('addEventListener("pointerdown"', text)
        self.assertIn("setPointerCapture", text)
        self.assertIn("clampGaugePan", text)
        self.assertIn("suppressVideoClick", text)
        self.assertIn('font-family: "Manrope"', text)
        self.assertIn('src: url("/font")', text)
        self.assertIn("Frames saved", text)
        self.assertIn("You can return to the terminal.", text)
        self.assertNotIn("#ff6b4a", text.lower())
        self.assertNotIn("#72b8d2", text.lower())
        self.assertNotIn("#94c7a4", text.lower())
        self.assertIn('id="jumpLaunch"', text)
        self.assertIn('id="jumpHundred"', text)
        self.assertIn("Use these frames", text)
        self.assertIn("heldStep", text)
        self.assertIn("const ARROW_HOLD_DELAY_MS = 350", text)
        self.assertIn("clearTimeout(holdTimer)", text)
        self.assertIn('addEventListener("keyup"', text)
        self.assertIn('navigator.sendBeacon("/cancel")', text)
        self.assertIn('window.addEventListener("pagehide"', text)
        self.assertNotIn("Consolas", text)
        self.assertNotIn("text-transform: uppercase", text)
        # The page must not auto-load external resources or upload data. The
        # only permitted absolute URL is the developer credit link.
        self.assertNotIn("http://", text)
        self.assertEqual(text.count("https://"), 1)
        self.assertIn('href="https://github.com/vroslmend"', text)
        for external_load in ('src="http', "url(http", "@import", 'fetch("http'):
            self.assertNotIn(external_load, text)

    def test_seek_nudge_scales_to_the_local_frame_gap(self) -> None:
        text = render_picker_html("sample_video.mp4")

        # A fixed 2 ms nudge overshoots frames on high-fps footage (>250 fps),
        # so the seek offset is capped to a fraction of the gap to the next frame.
        self.assertNotIn("times[requestedIndex] + 0.002", text)
        self.assertIn("Math.min(0.002, gap * 0.4)", text)

    def test_uses_an_inline_svg_favicon(self) -> None:
        text = render_picker_html("sample_video.mp4")

        self.assertIn('rel="icon"', text)
        self.assertIn("data:image/svg+xml;base64,", text)
        self.assertNotIn('href="data:,"', text)

    def test_escapes_video_name(self) -> None:
        text = render_picker_html('<video onload="bad">.mp4')

        self.assertNotIn('<video onload="bad">.mp4', text)
        self.assertIn("&lt;video onload=&quot;bad&quot;&gt;.mp4", text)


class RenderCalibrationHtmlTests(unittest.TestCase):
    def test_renders_click_steps_video_timing_and_local_submission(self) -> None:
        text = render_calibration_html("sample_video.mp4")

        self.assertIn("sample_video.mp4", text)
        self.assertIn('<video id="video" src="/video"', text)
        self.assertIn('<canvas id="overlay"', text)
        self.assertIn('fetch("/times")', text)
        self.assertIn("Click the needle pivot", text)
        self.assertIn("Click the needle tip at zero", text)
        self.assertIn("Click the 100 km/h mark", text)
        self.assertIn('fetch("/calibrate"', text)
        self.assertIn('navigator.sendBeacon("/cancel")', text)
        self.assertNotIn("http://", text)
        self.assertNotIn("https://", text)

    def test_escapes_video_name(self) -> None:
        text = render_calibration_html('<script class="bad">.mp4')

        self.assertNotIn('<script class="bad">.mp4', text)
        self.assertIn("&lt;script class=&quot;bad&quot;&gt;.mp4", text)

    def test_tracks_playback_so_clicks_stamp_the_visible_frame(self) -> None:
        text = render_calibration_html("sample_video.mp4")

        self.assertIn('video.addEventListener("pause", syncSelected)', text)
        self.assertIn('video.addEventListener("timeupdate"', text)
        self.assertIn("video.seeking", text)


class PickerServerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tempdir.name)
        frames_dir = self.workdir / "frames"
        frames_dir.mkdir()
        (frames_dir / "000001.jpg").write_bytes(b"a")
        (frames_dir / "000002.jpg").write_bytes(b"b")
        self.video_bytes = bytes(range(250)) * 4
        self.video_path = self.workdir / "video.mp4"
        self.video_path.write_bytes(self.video_bytes)
        self.server = _PickerServer(
            self.video_path, self.workdir, [0.0, 0.5, 1.0]
        )
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()
        self.tempdir.cleanup()

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[http.client.HTTPResponse, bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=2)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        return response, payload

    def test_serves_times_video_range_rejects_traversal_and_accepts_marks(self) -> None:
        response, payload = self.request("GET", "/times")
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(payload), [0.0, 0.5, 1.0])

        response, payload = self.request(
            "GET", "/video", headers={"Range": "bytes=100-199"}
        )
        self.assertEqual(response.status, 206)
        self.assertEqual(response.getheader("Content-Range"), "bytes 100-199/1000")
        self.assertEqual(response.getheader("Accept-Ranges"), "bytes")
        self.assertEqual(payload, self.video_bytes[100:200])

        response, _ = self.request("GET", "/thumbs/../x")
        self.assertGreaterEqual(response.status, 400)

        body = json.dumps({"launch": 0.5, "hundred": 1.0}).encode("utf-8")
        response, payload = self.request(
            "POST",
            "/done",
            body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(payload), {"ok": True})
        self.assertTrue(self.server.result_event.wait(timeout=1))
        self.assertEqual(self.server.result, (0.5, 1.0))

    def test_serves_page_full_video_and_thumbnail(self) -> None:
        response, payload = self.request("GET", "/")
        self.assertEqual(response.status, 200)
        self.assertIn(b"<video", payload)

        response, payload = self.request("GET", "/video")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Accept-Ranges"), "bytes")
        self.assertEqual(payload, self.video_bytes)

        response, payload = self.request("GET", "/font")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "font/ttf")
        self.assertGreater(len(payload), 50_000)

        response, payload = self.request("GET", "/thumbs/000001.jpg")
        self.assertEqual(response.status, 200)
        self.assertEqual(payload, b"a")

    def test_rejects_invalid_done_payload(self) -> None:
        response, _ = self.request(
            "POST",
            "/done",
            body=b'{"launch": true}',
            headers={"Content-Type": "application/json", "Content-Length": "16"},
        )

        self.assertEqual(response.status, 400)
        self.assertFalse(self.server.result_event.is_set())
        self.assertIsNone(self.server.result)


class CalibrationServerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tempdir.name)
        self.video_path = self.workdir / "video.mp4"
        self.video_path.write_bytes(bytes(range(250)) * 4)
        self.server = _CalibrationServer(
            self.video_path,
            self.workdir,
            [0.0, 0.5, 1.0],
        )
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()
        self.tempdir.cleanup()

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[http.client.HTTPResponse, bytes]:
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.port, timeout=2
        )
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        return response, payload

    def test_serves_calibration_page_and_accepts_normalized_points(self) -> None:
        response, payload = self.request("GET", "/")
        self.assertEqual(response.status, 200)
        self.assertIn(b"Click the needle pivot", payload)

        body = json.dumps(
            {
                "pivot": [0.5, 0.6],
                "zero": [0.2, 0.8],
                "hundred": [0.8, 0.3],
                "frame": 0.5,
            }
        ).encode("utf-8")
        response, payload = self.request(
            "POST",
            "/calibrate",
            body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(payload), {"ok": True})
        self.assertTrue(self.server.result_event.wait(timeout=1))
        self.assertEqual(
            self.server.calibration_result,
            Calibration(
                pivot=(0.5, 0.6),
                zero=(0.2, 0.8),
                hundred=(0.8, 0.3),
                frame=0.5,
            ),
        )

    def test_rejects_invalid_calibration_without_unblocking(self) -> None:
        body = json.dumps(
            {
                "pivot": [2.0, 0.6],
                "zero": [0.2, 0.8],
                "hundred": [0.8, 0.3],
                "frame": 0.5,
            }
        ).encode("utf-8")
        response, _ = self.request(
            "POST",
            "/calibrate",
            body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        )

        self.assertEqual(response.status, 400)
        self.assertFalse(self.server.result_event.is_set())
        self.assertIsNone(self.server.calibration_result)

    def test_cancel_unblocks_server_without_a_result(self) -> None:
        response, payload = self.request("POST", "/cancel", body=b"")

        self.assertEqual(response.status, 204)
        self.assertEqual(payload, b"")
        self.assertTrue(self.server.result_event.wait(timeout=1))
        self.assertTrue(self.server.cancelled)
        self.assertIsNone(self.server.result)


class ServeCalibrationTests(unittest.TestCase):
    class FakeServer:
        instance = None

        def __init__(self, video_path, workdir, times, *, video_name=None):
            type(self).instance = self
            self.video_path = video_path
            self.video_name = video_name
            self.url = "http://127.0.0.1:12345/"
            self.calibration_result = Calibration(
                pivot=(0.5, 0.6),
                zero=(0.2, 0.8),
                hundred=(0.8, 0.3),
                frame=0.5,
            )
            self.cancelled = False
            self.result_event = mock.Mock()
            self.started = False
            self.stopped = False

        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

    def test_returns_browser_calibration_and_stops_server(self) -> None:
        toolchain = Toolchain(ffmpeg="ffmpeg", ffprobe="ffprobe")

        with mock.patch(
            "zero2hundred.picker.prepare_browser_video",
            return_value=Path("browser-preview.mp4"),
        ):
            with mock.patch(
                "zero2hundred.picker._CalibrationServer", self.FakeServer
            ):
                with mock.patch(
                    "zero2hundred.picker.webbrowser.open"
                ) as open_browser:
                    result = serve_calibration(
                        Path("input.mp4"), toolchain, [0.0, 0.5], Path("work")
                    )

        self.assertEqual(result, self.FakeServer.instance.calibration_result)
        self.assertTrue(self.FakeServer.instance.started)
        self.assertTrue(self.FakeServer.instance.stopped)
        self.assertEqual(self.FakeServer.instance.video_name, "input.mp4")
        self.FakeServer.instance.result_event.wait.assert_called_once_with(0.1)
        open_browser.assert_called_once_with("http://127.0.0.1:12345/")


class ServePickerTests(unittest.TestCase):
    class FakeServer:
        instance = None

        def __init__(self, video_path, workdir, times, *, video_name=None):
            type(self).instance = self
            self.video_path = video_path
            self.video_name = video_name
            self.url = "http://127.0.0.1:12345/"
            self.result = (0.5, 1.0)
            self.cancelled = False
            self.result_event = mock.Mock()
            self.started = False
            self.stopped = False

        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

    def test_warns_on_thumbnail_mismatch_and_returns_server_result(self) -> None:
        toolchain = Toolchain(ffmpeg="ffmpeg", ffprobe="ffprobe")
        times = [0.0, 1.0, 2.0, 3.0, 4.0]
        thumbnails = [Path("1.jpg"), Path("2.jpg"), Path("3.jpg")]
        stderr = io.StringIO()

        with mock.patch("zero2hundred.picker.extract_thumbnails", return_value=thumbnails):
            with mock.patch(
                "zero2hundred.picker.prepare_browser_video",
                return_value=Path("browser-preview.mp4"),
            ):
                with mock.patch("zero2hundred.picker._PickerServer", self.FakeServer):
                    with mock.patch("zero2hundred.picker.webbrowser.open") as open_browser:
                        with contextlib.redirect_stderr(stderr):
                            with contextlib.redirect_stdout(io.StringIO()):
                                result = serve_picker(
                                    Path("input.mp4"), toolchain, times, Path("work")
                                )

        self.assertEqual(result, (0.5, 1.0))
        self.assertIn("expected 5 thumbnails", stderr.getvalue())
        self.assertIn("produced 3", stderr.getvalue())
        self.assertTrue(self.FakeServer.instance.started)
        self.assertTrue(self.FakeServer.instance.stopped)
        self.assertEqual(
            self.FakeServer.instance.video_path, Path("browser-preview.mp4")
        )
        self.assertEqual(self.FakeServer.instance.video_name, "input.mp4")
        self.FakeServer.instance.result_event.wait.assert_called_once_with(0.1)
        open_browser.assert_called_once_with("http://127.0.0.1:12345/")

    def test_prints_preparation_status_and_passes_duration(self) -> None:
        toolchain = Toolchain(ffmpeg="ffmpeg", ffprobe="ffprobe")
        times = [0.0, 1.0, 2.0]
        captured: dict[str, float] = {}
        stdout = io.StringIO()

        def fake_prepare(path, tool, workdir, *, duration=0.0):
            captured["duration"] = duration
            return Path("browser-preview.mp4")

        with mock.patch(
            "zero2hundred.picker.extract_thumbnails",
            return_value=[Path("1.jpg"), Path("2.jpg"), Path("3.jpg")],
        ):
            with mock.patch(
                "zero2hundred.picker.prepare_browser_video", side_effect=fake_prepare
            ):
                with mock.patch("zero2hundred.picker._PickerServer", self.FakeServer):
                    with mock.patch("zero2hundred.picker.webbrowser.open"):
                        with contextlib.redirect_stdout(stdout):
                            result = serve_picker(
                                Path("input.mp4"), toolchain, times, Path("work")
                            )

        self.assertEqual(result, (0.5, 1.0))
        self.assertEqual(captured["duration"], 2.0)
        output = stdout.getvalue()
        self.assertIn("Extracting preview frames...", output)
        self.assertIn(
            "Waiting for launch and 100 km/h marks in the browser...", output
        )

    def test_stops_server_before_reraising_keyboard_interrupt(self) -> None:
        toolchain = Toolchain(ffmpeg="ffmpeg", ffprobe="ffprobe")

        with mock.patch(
            "zero2hundred.picker.extract_thumbnails", return_value=[Path("1.jpg")]
        ):
            with mock.patch(
                "zero2hundred.picker.prepare_browser_video",
                return_value=Path("input.mp4"),
            ):
                with mock.patch("zero2hundred.picker._PickerServer", self.FakeServer):
                    with mock.patch("zero2hundred.picker.webbrowser.open"):
                        self.FakeServer.instance = None
                        with self.assertRaises(KeyboardInterrupt):
                            original_init = self.FakeServer.__init__

                            def init_with_interrupt(server, *args, **kwargs):
                                original_init(server, *args, **kwargs)
                                server.result_event.wait.side_effect = [
                                    False,
                                    KeyboardInterrupt,
                                ]

                            with mock.patch.object(
                                self.FakeServer, "__init__", init_with_interrupt
                            ):
                                with contextlib.redirect_stdout(io.StringIO()):
                                    serve_picker(
                                        Path("input.mp4"), toolchain, [0.0], Path("work")
                                    )

        self.assertTrue(self.FakeServer.instance.stopped)
        self.assertEqual(self.FakeServer.instance.result_event.wait.call_count, 2)
        self.FakeServer.instance.result_event.wait.assert_called_with(0.1)

    def test_browser_cancel_stops_server_and_cancels_cli_wait(self) -> None:
        toolchain = Toolchain(ffmpeg="ffmpeg", ffprobe="ffprobe")
        original_init = self.FakeServer.__init__

        def cancel_on_init(server, *args, **kwargs):
            original_init(server, *args, **kwargs)
            server.cancelled = True

        with mock.patch(
            "zero2hundred.picker.extract_thumbnails", return_value=[Path("1.jpg")]
        ):
            with mock.patch(
                "zero2hundred.picker.prepare_browser_video",
                return_value=Path("input.mp4"),
            ):
                with mock.patch("zero2hundred.picker._PickerServer", self.FakeServer):
                    with mock.patch.object(
                        self.FakeServer, "__init__", cancel_on_init
                    ):
                        with mock.patch("zero2hundred.picker.webbrowser.open"):
                            with self.assertRaises(KeyboardInterrupt):
                                with contextlib.redirect_stdout(io.StringIO()):
                                    serve_picker(
                                        Path("input.mp4"), toolchain, [0.0], Path("work")
                                    )

        self.assertTrue(self.FakeServer.instance.stopped)


if __name__ == "__main__":
    unittest.main()
