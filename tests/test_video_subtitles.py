from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
import types
import unittest


SRC_PATH = str(Path(__file__).resolve().parents[1] / "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

TEST_CLIP_WIDTH = 1080
TEST_FONT_SIZE = 84
EXPECTED_AUDIO_DURATION = 2.75


def _load_video_module() -> types.ModuleType:
    if "whisper" not in sys.modules:
        whisper = types.ModuleType("whisper")
        whisper.load_model = lambda *args, **kwargs: None
        sys.modules["whisper"] = whisper

    module_name = "video_test_module"
    spec = importlib.util.spec_from_file_location(
        module_name,
        str(Path(__file__).resolve().parents[1] / "src" / "video.py"),
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class TestVideoSubtitleRendering(unittest.TestCase):
    @unittest.skipUnless(
        importlib.util.find_spec("moviepy") is not None and importlib.util.find_spec("numpy") is not None,
        "moviepy and numpy are required for subtitle rendering tests",
    )
    def test_build_subtitle_clips_uses_image_clips_and_bounds_width(self) -> None:
        video = _load_video_module()
        subtitles = [
            video.SubtitleChunk(
                text="this is a fairly long subtitle sentence that should wrap across lines",
                start=0.0,
                end=1.25,
            ),
            video.SubtitleChunk(text="short text", start=1.25, end=2.0),
        ]

        clips = video._build_subtitle_clips(
            subtitles=subtitles,
            clip_width=TEST_CLIP_WIDTH,
            font_size=TEST_FONT_SIZE,
            stroke_width=6,
        )
        try:
            expected_max_width = TEST_CLIP_WIDTH - video.SUBTITLE_SIDE_MARGIN
            expected_min_height = TEST_FONT_SIZE + (max(video.SUBTITLE_MIN_VERTICAL_PADDING, TEST_FONT_SIZE // 4) * 2)
            self.assertEqual(len(clips), 2)
            self.assertAlmostEqual(clips[0].start, 0.0)
            self.assertAlmostEqual(clips[0].end, 1.25)
            self.assertGreater(clips[0].w, 0)
            self.assertGreater(clips[0].h, 0)
            self.assertLessEqual(clips[0].w, expected_max_width)
            self.assertGreaterEqual(clips[0].h, expected_min_height)
            self.assertEqual(clips[0].__class__.__name__, "ImageClip")
        finally:
            for clip in clips:
                clip.close()

    @unittest.skipUnless(
        importlib.util.find_spec("moviepy") is not None and importlib.util.find_spec("numpy") is not None,
        "moviepy and numpy are required for video generation tests",
    )
    def test_generate_video_aligns_audio_and_uses_unique_temp_audiofile(self) -> None:
        video = _load_video_module()

        class _FakeAudioClip:
            def __init__(self, _: str) -> None:
                self.duration = EXPECTED_AUDIO_DURATION
                self.start_value = None
                self.duration_value = None

            def set_start(self, value: float) -> "_FakeAudioClip":
                self.start_value = value
                return self

            def set_duration(self, value: float) -> "_FakeAudioClip":
                self.duration_value = value
                return self

            def close(self) -> None:
                return None

        class _FakeBaseClip:
            def __init__(self) -> None:
                self.w = TEST_CLIP_WIDTH
                self.fps = 30

            def close(self) -> None:
                return None

        created: dict[str, object] = {}

        class _FakeFinalClip:
            def __init__(self, clips: list[object]) -> None:
                self.clips = clips
                self.duration_value = None
                self.audio_value = None
                self.write_kwargs = {}
                created["final"] = self

            def set_duration(self, value: float) -> "_FakeFinalClip":
                self.duration_value = value
                return self

            def set_audio(self, clip: object) -> "_FakeFinalClip":
                self.audio_value = clip
                return self

            def write_videofile(self, path: str, **kwargs) -> None:
                self.write_kwargs = kwargs
                Path(path).write_bytes(b"ok")

            def close(self) -> None:
                return None

        class _FakeSubtitleClip:
            def close(self) -> None:
                return None

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            audio_file = tmp_path / "voice.mp3"
            background_file = tmp_path / "bg.mp4"
            output_file = tmp_path / "final_test.mp4"
            audio_file.write_bytes(b"a")
            background_file.write_bytes(b"b")

            def _fake_audio_factory(path: str):
                clip = _FakeAudioClip(path)
                created["audio"] = clip
                return clip

            video.AudioFileClip = _fake_audio_factory
            video._prepare_background_clip = lambda *args, **kwargs: _FakeBaseClip()
            video._extract_word_tokens = lambda *args, **kwargs: [video.WordToken(text="hello", start=0.0, end=0.5)]
            video._group_words = lambda *args, **kwargs: [video.SubtitleChunk(text="HELLO", start=0.0, end=0.5)]
            video._build_subtitle_clips = lambda *args, **kwargs: [_FakeSubtitleClip()]
            video.CompositeVideoClip = lambda clips: _FakeFinalClip(clips)
            video._build_output_path = lambda *args, **kwargs: output_file
            video.get_config = lambda: {
                "video": {"subtitle": {}, "output": {}},
                "paths": {"output_dir": str(tmp_path), "background_video": str(background_file)},
            }

            result = video.generate_video(
                audio_path=str(audio_file),
                background_video_path=str(background_file),
                output_dir=str(tmp_path),
                whisper_model_name="base",
            )

            audio_clip = created["audio"]
            final_clip = created["final"]
            self.assertEqual(audio_clip.start_value, 0)
            self.assertAlmostEqual(audio_clip.duration_value, EXPECTED_AUDIO_DURATION)
            self.assertAlmostEqual(final_clip.duration_value, EXPECTED_AUDIO_DURATION)
            self.assertIs(final_clip.audio_value, audio_clip)
            self.assertEqual(
                final_clip.write_kwargs.get("temp_audiofile"),
                str(tmp_path / "final_test-temp-audio.m4a"),
            )
            self.assertEqual(result, str(output_file))


if __name__ == "__main__":
    unittest.main()
