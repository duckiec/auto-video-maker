from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
import types
import unittest
import importlib


SRC_PATH = str(Path(__file__).resolve().parents[1] / "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)


def _load_video_module() -> types.ModuleType:
    if "whisper" not in sys.modules:
        whisper = types.ModuleType("whisper")
        whisper.load_model = lambda *args, **kwargs: None
        sys.modules["whisper"] = whisper

    module_name = "video_real_for_tests"
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
            clip_width=1080,
            font_size=84,
            stroke_width=6,
        )
        try:
            self.assertEqual(len(clips), 2)
            self.assertAlmostEqual(clips[0].start, 0.0)
            self.assertAlmostEqual(clips[0].end, 1.25)
            self.assertGreater(clips[0].w, 0)
            self.assertGreater(clips[0].h, 0)
            self.assertLessEqual(clips[0].w, 960)
            self.assertEqual(clips[0].__class__.__name__, "ImageClip")
        finally:
            for clip in clips:
                clip.close()


if __name__ == "__main__":
    unittest.main()
