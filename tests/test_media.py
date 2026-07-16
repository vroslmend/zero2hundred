from pathlib import Path
import unittest

from zero2hundred.errors import MediaError
from zero2hundred.media import parse_media_info


def _video_stream(**overrides) -> dict:
    stream = {
        "codec_type": "video",
        "codec_name": "h264",
        "width": 1920,
        "height": 1080,
        "r_frame_rate": "30/1",
        "avg_frame_rate": "30/1",
        "duration": "10.0",
    }
    stream.update(overrides)
    return stream


def _payload(video: dict, audio: dict | None = None) -> dict:
    streams = [video]
    if audio is not None:
        streams.append(audio)
    return {
        "streams": streams,
        "format": {"duration": "10.0"},
    }


AUDIO_STREAM = {"codec_type": "audio", "codec_name": "aac"}


class ParseMediaInfoTests(unittest.TestCase):
    def test_landscape_without_side_data(self) -> None:
        payload = _payload(_video_stream(), AUDIO_STREAM)
        info = parse_media_info(payload, Path("clip.mp4"))
        self.assertEqual(info.width, 1920)
        self.assertEqual(info.height, 1080)
        self.assertEqual(info.rotation, 0)

    def test_portrait_rotation_swaps_dimensions(self) -> None:
        video = _video_stream(
            side_data_list=[{"side_data_type": "Display Matrix", "rotation": -90}]
        )
        payload = _payload(video, AUDIO_STREAM)
        info = parse_media_info(payload, Path("clip.mp4"))
        self.assertEqual(info.width, 1080)
        self.assertEqual(info.height, 1920)
        self.assertEqual(info.rotation, 270)

    def test_rotation_180_does_not_swap(self) -> None:
        video = _video_stream(
            side_data_list=[{"side_data_type": "Display Matrix", "rotation": 180}]
        )
        payload = _payload(video, AUDIO_STREAM)
        info = parse_media_info(payload, Path("clip.mp4"))
        self.assertEqual(info.width, 1920)
        self.assertEqual(info.height, 1080)
        self.assertEqual(info.rotation, 180)

    def test_garbage_side_data_falls_back_to_zero(self) -> None:
        video = _video_stream(
            side_data_list=[{"side_data_type": "Display Matrix", "rotation": "abc"}]
        )
        payload = _payload(video, AUDIO_STREAM)
        info = parse_media_info(payload, Path("clip.mp4"))
        self.assertEqual(info.rotation, 0)

        video = _video_stream(side_data_list="not-a-list")
        payload = _payload(video, AUDIO_STREAM)
        info = parse_media_info(payload, Path("clip.mp4"))
        self.assertEqual(info.rotation, 0)

    def test_frame_rate_falls_back_to_r_frame_rate(self) -> None:
        video = _video_stream(avg_frame_rate="0/0", r_frame_rate="25/1")
        payload = _payload(video)
        info = parse_media_info(payload, Path("clip.mp4"))
        self.assertEqual(info.frame_rate, 25.0)

    def test_frame_rate_falls_back_to_default(self) -> None:
        video = _video_stream(avg_frame_rate="0/0", r_frame_rate="0/0")
        payload = _payload(video)
        info = parse_media_info(payload, Path("clip.mp4"))
        self.assertEqual(info.frame_rate, 30.0)

    def test_no_audio_stream(self) -> None:
        payload = _payload(_video_stream())
        info = parse_media_info(payload, Path("clip.mp4"))
        self.assertFalse(info.has_audio)

    def test_missing_video_stream_raises(self) -> None:
        payload = {"streams": [AUDIO_STREAM], "format": {"duration": "10.0"}}
        with self.assertRaises(MediaError):
            parse_media_info(payload, Path("clip.mp4"))


if __name__ == "__main__":
    unittest.main()
