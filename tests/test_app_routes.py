from __future__ import annotations

import importlib
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
import io


SRC_PATH = str(Path(__file__).resolve().parents[1] / "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)


if "bot" not in sys.modules:
    bot = types.ModuleType("bot")
    bot.run_pipeline = lambda: None
    bot.start_scheduler_loop = lambda: None
    sys.modules["bot"] = bot

try:
    app_module = importlib.import_module("app")
except ModuleNotFoundError as exc:
    if exc.name == "flask":
        app_module = None
    else:
        raise


class _DummyThread:
    def __init__(self, target=None, daemon=None, name=None):
        self.target = target
        self.daemon = daemon
        self.name = name
        self.started = False

    def start(self) -> None:
        self.started = True


class TestAppRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if app_module is None:
            raise unittest.SkipTest("Flask dependency is not installed in this test environment")

    def setUp(self) -> None:
        self.client = app_module.app.test_client()

    def test_health_endpoint(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "ok"})

    def test_job_status_endpoint(self) -> None:
        with patch.dict(
            app_module.JOB_STATE,
            {
                "running": True,
                "last_status": "running",
                "stage": "generating_audio",
                "progress": 45,
                "last_message": "Generating voiceover audio...",
                "last_video_filename": None,
            },
            clear=False,
        ):
            response = self.client.get("/job-status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["stage"], "generating_audio")
        self.assertEqual(response.json["progress"], 45)

    def test_index_renders_with_history(self) -> None:
        config = {"paths": {"history_db": "history.db"}}
        history = [{"created_at": "now", "source": "ai", "title": "hello", "video_filename": "v.mp4"}]
        with (
            patch("app.get_config", return_value=config),
            patch("app.fetch_recent_history", return_value=history),
            patch("app.init_db"),
            patch("app._start_scheduler_thread_once"),
        ):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"hello", response.data)

    def test_generate_now_starts_thread_when_not_running(self) -> None:
        with (
            patch.dict(app_module.JOB_STATE, {"running": False}, clear=False),
            patch("app.threading.Thread", _DummyThread),
            patch("app.get_config", return_value={"paths": {"history_db": "history.db"}}),
            patch("app.init_db"),
            patch("app._start_scheduler_thread_once"),
        ):
            response = self.client.post("/generate-now")

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("/"))

    def test_save_settings_updates_and_persists_config(self) -> None:
        base = {
            "scheduler": {"times": ["08:00"], "extra_times": [], "run_on_start": False},
            "paths": {
                "background_video": "assets/gameplay.mp4",
                "history_db": "history.db",
                "output_dir": "output",
                "cookies_dir": "cookies",
            },
            "audio": {"voice": "v"},
            "video": {"whisper_model": "base"},
            "scrapers": {"selection_pool": ["reddit"], "reddit": {"subreddits": ["AskReddit"]}, "ai": {"model": "m"}},
            "uploader": {"enabled": True, "platform": "random", "headless": True, "base_tags": ["#shorts"]},
        }

        with (
            patch("app.get_config", return_value=base),
            patch("app.save_config") as save_mock,
            patch("app.init_db"),
            patch("app._start_scheduler_thread_once"),
        ):
            response = self.client.post(
                "/settings",
                data={
                    "scheduler_times": "09:00, 18:00",
                    "scheduler_extra_times": "12:00",
                    "run_on_start": "on",
                    "background_video": "assets/new.mp4",
                    "audio_voice": "voice2",
                    "whisper_model": "small",
                    "selection_pool": "wiki,ai",
                    "reddit_subreddits": "news,worldnews",
                    "openrouter_model": "model-x",
                    "uploader_platform": "youtube",
                    "uploader_enabled": "on",
                    "uploader_headless": "on",
                    "uploader_base_tags": "#a,#b",
                },
            )

        self.assertEqual(response.status_code, 302)
        saved = save_mock.call_args.args[0]
        self.assertEqual(saved["scheduler"]["times"], ["09:00", "18:00"])
        self.assertEqual(saved["scheduler"]["extra_times"], ["12:00"])
        self.assertTrue(saved["scheduler"]["run_on_start"])
        self.assertEqual(saved["paths"]["background_video"], "assets/new.mp4")
        self.assertEqual(saved["scrapers"]["ai"]["model"], "model-x")
        self.assertTrue(saved["uploader"]["enabled"])
        self.assertEqual(saved["uploader"]["platform"], "youtube")
        self.assertEqual(saved["uploader"]["base_tags"], ["#a", "#b"])

    def test_save_settings_prefers_picker_when_manual_model_empty(self) -> None:
        base = {
            "scheduler": {"times": ["08:00"], "extra_times": [], "run_on_start": False},
            "paths": {"background_video": "assets/gameplay.mp4", "history_db": "history.db"},
            "audio": {"voice": "v"},
            "video": {"whisper_model": "base"},
            "scrapers": {"selection_pool": ["reddit"], "reddit": {"subreddits": ["AskReddit"]}, "ai": {"model": "m"}},
            "uploader": {"enabled": True, "platform": "random", "headless": True, "base_tags": ["#shorts"]},
        }

        with (
            patch("app.get_config", return_value=base),
            patch("app.save_config") as save_mock,
            patch("app.init_db"),
            patch("app._start_scheduler_thread_once"),
        ):
            response = self.client.post(
                "/settings",
                data={
                    "openrouter_model": "",
                    "openrouter_model_picker": "google/gemini-2.5-pro",
                },
            )

        self.assertEqual(response.status_code, 302)
        saved = save_mock.call_args.args[0]
        self.assertEqual(saved["scrapers"]["ai"]["model"], "google/gemini-2.5-pro")

    def test_prepare_setup_creates_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "out"
            cookies_dir = Path(tmp) / "cookiesx"
            background_video = Path(tmp) / "assetsx" / "gameplay.mp4"
            config = {
                "paths": {
                    "history_db": str(Path(tmp) / "history.db"),
                    "output_dir": str(output_dir),
                    "cookies_dir": str(cookies_dir),
                    "background_video": str(background_video),
                }
            }

            with (
                patch("app.get_config", return_value=config),
                patch("app.init_db"),
                patch("app._start_scheduler_thread_once"),
            ):
                response = self.client.post("/setup/prepare")

            self.assertEqual(response.status_code, 302)
            self.assertTrue(output_dir.exists())
            self.assertTrue(cookies_dir.exists())
            self.assertTrue(background_video.parent.exists())

    def test_upload_background_asset_saves_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            background_video = Path(tmp) / "assetsx" / "gameplay.mp4"
            config = {
                "paths": {
                    "history_db": str(Path(tmp) / "history.db"),
                    "output_dir": str(Path(tmp) / "output"),
                    "cookies_dir": str(Path(tmp) / "cookies"),
                    "background_video": str(background_video),
                }
            }
            data = {
                "background_video_file": (io.BytesIO(b"fake-video-bytes"), "bg.mp4"),
            }
            with (
                patch("app.get_config", return_value=config),
                patch("app.init_db"),
                patch("app._start_scheduler_thread_once"),
            ):
                response = self.client.post(
                    "/setup/upload-background",
                    data=data,
                    content_type="multipart/form-data",
                )

            self.assertEqual(response.status_code, 302)
            self.assertTrue(background_video.exists())
            self.assertEqual(background_video.read_bytes(), b"fake-video-bytes")

    def test_video_file_rejects_path_traversal(self) -> None:
        config = {"paths": {"history_db": "history.db", "output_dir": "output"}}
        with (
            patch("app.get_config", return_value=config),
            patch("app.init_db"),
            patch("app._start_scheduler_thread_once"),
        ):
            response = self.client.get("/videos/../../etc/passwd")

        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
