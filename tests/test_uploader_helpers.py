from __future__ import annotations

import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_PATH = "/home/runner/work/auto-video-maker/auto-video-maker/src"
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)


def _install_uploader_import_stubs() -> None:
    if "dotenv" not in sys.modules:
        dotenv = types.ModuleType("dotenv")
        dotenv.load_dotenv = lambda: None
        sys.modules["dotenv"] = dotenv

    if "playwright" not in sys.modules:
        sys.modules["playwright"] = types.ModuleType("playwright")

    if "playwright.sync_api" not in sys.modules:
        sync_api = types.ModuleType("playwright.sync_api")

        class _TimeoutError(Exception):
            pass

        sync_api.BrowserContext = object
        sync_api.Page = object
        sync_api.TimeoutError = _TimeoutError
        sync_api.sync_playwright = lambda: None
        sys.modules["playwright.sync_api"] = sync_api


_install_uploader_import_stubs()
uploader = importlib.import_module("uploader")


class TestUploaderHelpers(unittest.TestCase):
    def test_generate_title_handles_empty_and_limits_length(self) -> None:
        title = uploader._generate_title("", max_len=30)
        self.assertTrue(title.endswith("..."))
        self.assertLessEqual(len(title), 30)

    def test_extract_tags_uses_base_and_unique_dynamic_tags(self) -> None:
        text = "Alpha beta alpha gamma delta epsilon"
        with patch("uploader.get_config", return_value={"uploader": {"base_tags": ["#base1", "#base2"]}}):
            tags = uploader._extract_tags(text, limit=3)

        self.assertIn("#base1", tags)
        self.assertIn("#base2", tags)
        self.assertIn("#alpha", tags)
        self.assertIn("#beta", tags)
        self.assertIn("#gamma", tags)
        self.assertNotIn("#delta", tags)

    def test_ensure_file_exists_raises_for_missing_path(self) -> None:
        with self.assertRaises(uploader.UploadError):
            uploader._ensure_file_exists("/definitely/missing.file", "Video file")

    def test_upload_video_rejects_unsupported_platform_before_playwright(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            video_path = Path(tmp) / "video.mp4"
            video_path.write_bytes(b"x")
            with self.assertRaises(uploader.UploadError):
                uploader.upload_video(video_path=str(video_path), source_text="abc", platform="instagram")


if __name__ == "__main__":
    unittest.main()
