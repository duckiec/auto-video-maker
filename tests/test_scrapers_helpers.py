from __future__ import annotations

import importlib
import sys
from pathlib import Path
import types
import unittest
from unittest.mock import patch


SRC_PATH = str(Path(__file__).resolve().parents[1] / "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)


def _install_scraper_import_stubs() -> None:
    if "dotenv" not in sys.modules:
        dotenv = types.ModuleType("dotenv")
        dotenv.load_dotenv = lambda: None
        sys.modules["dotenv"] = dotenv

    if "praw" not in sys.modules:
        praw = types.ModuleType("praw")
        praw.Reddit = object
        sys.modules["praw"] = praw

    if "requests" not in sys.modules:
        requests = types.ModuleType("requests")
        requests.Session = object
        sys.modules["requests"] = requests

    if "wikipediaapi" not in sys.modules:
        wikipediaapi = types.ModuleType("wikipediaapi")
        wikipediaapi.Wikipedia = object
        sys.modules["wikipediaapi"] = wikipediaapi

    if "openai" not in sys.modules:
        openai = types.ModuleType("openai")
        openai.OpenAI = object
        sys.modules["openai"] = openai


_install_scraper_import_stubs()
scrapers = importlib.import_module("scrapers")


def _set_shuffle_order(*ordered_values: str):
    def _apply(items: list[str]) -> None:
        items[:] = list(ordered_values)

    return _apply


class TestScrapersHelpers(unittest.TestCase):
    def test_retry_retries_then_succeeds(self) -> None:
        calls = {"count": 0}

        def flaky() -> str:
            calls["count"] += 1
            if calls["count"] < 3:
                raise ValueError("boom")
            return "ok"

        with patch("scrapers.time.sleep") as sleep_mock:
            result = scrapers._retry(flaky, attempts=3, delay_seconds=0.1)

        self.assertEqual(result, "ok")
        self.assertEqual(calls["count"], 3)
        self.assertEqual(sleep_mock.call_count, 2)

    def test_retry_raises_on_empty_result(self) -> None:
        with self.assertRaises(scrapers.ScraperError):
            scrapers._retry(lambda: "", attempts=1)

    def test_get_random_content_falls_back_when_pool_invalid(self) -> None:
        with (
            patch("scrapers.get_config", return_value={"scrapers": {"selection_pool": ["unknown"]}}),
            patch("scrapers.get_reddit_story", return_value="reddit text"),
            patch("scrapers.get_wiki_fact", return_value="wiki text"),
            patch("scrapers.get_ai_story", return_value="ai text"),
            patch("scrapers.random.shuffle", side_effect=_set_shuffle_order("wiki", "ai", "reddit")),
        ):
            result = scrapers.get_random_content()

        self.assertEqual(result, "wiki text")

    def test_get_random_content_skips_reddit_when_credentials_missing(self) -> None:
        with (
            patch("scrapers.get_config", return_value={"scrapers": {"selection_pool": ["reddit", "wiki"]}}),
            patch("scrapers.get_reddit_story", return_value="reddit text"),
            patch("scrapers.get_wiki_fact", return_value="wiki text"),
            patch("scrapers.get_ai_story", return_value="ai text"),
            patch("scrapers.has_reddit_credentials", return_value=False),
            patch("scrapers.random.shuffle", side_effect=_set_shuffle_order("reddit", "wiki")),
            patch("scrapers.LOGGER.warning") as warning_mock,
        ):
            result = scrapers.get_random_content()

        self.assertEqual(result, "wiki text")
        warning_mock.assert_called_once_with("Missing Reddit credentials, falling back to next source...")

    def test_get_random_content_raises_when_only_reddit_without_credentials(self) -> None:
        with (
            patch("scrapers.get_config", return_value={"scrapers": {"selection_pool": ["reddit"]}}),
            patch("scrapers.has_reddit_credentials", return_value=False),
            patch("scrapers.random.shuffle", side_effect=_set_shuffle_order("reddit")),
        ):
            with self.assertRaises(scrapers.ScraperError):
                scrapers.get_random_content()

    def test_get_ai_story_falls_back_when_model_has_no_endpoints(self) -> None:
        class _NoEndpointsError(RuntimeError):
            pass

        calls = {"count": 0}

        def _create(*, model: str, **kwargs):  # noqa: ANN003
            calls["count"] += 1
            if calls["count"] == 1:
                raise _NoEndpointsError(
                    "Error code: 404 - {'error': {'message': 'No endpoints found for deepseek/deepseek-chat-v3-0324:free.', 'code': 404}}"
                )
            self.assertEqual(model, "google/gemini-2.5-flash:free")
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="word " * 80))]
            )

        fake_client = types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=_create))
        )
        config = {
            "scrapers": {
                "ai": {
                    "target_words": 100,
                    "max_words": 140,
                    "min_words": 60,
                    "model": "deepseek/deepseek-chat-v3-0324:free",
                }
            },
            "api": {
                "openrouter_base_url": "https://openrouter.ai/api/v1",
                "openrouter_referer": "https://local.video-factory",
                "openrouter_title": "Auto Video Maker",
            },
        }

        with (
            patch("scrapers.get_config", return_value=config),
            patch("scrapers.os.getenv", side_effect=lambda key, default=None: "k" if key == "OPENROUTER_API_KEY" else default),
            patch("scrapers.OpenAI", return_value=fake_client),
            patch("scrapers.get_openrouter_models", return_value=["deepseek/deepseek-chat-v3-0324:free", "google/gemini-2.5-flash:free"]),
        ):
            result = scrapers.get_ai_story()

        self.assertGreaterEqual(len(result.split()), 60)
        self.assertEqual(calls["count"], 2)

    def test_get_ai_story_does_not_mask_original_error_when_fallback_lookup_fails(self) -> None:
        class _NoEndpointsError(RuntimeError):
            pass

        def _create(**kwargs):  # noqa: ANN003
            raise _NoEndpointsError(
                "Error code: 404 - {'error': {'message': 'No endpoints found for deepseek/deepseek-chat-v3-0324:free.', 'code': 404}}"
            )

        fake_client = types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=_create))
        )
        config = {
            "scrapers": {"ai": {"model": "deepseek/deepseek-chat-v3-0324:free"}},
            "api": {"openrouter_base_url": "https://openrouter.ai/api/v1"},
        }

        with (
            patch("scrapers.get_config", return_value=config),
            patch("scrapers.os.getenv", side_effect=lambda key, default=None: "k" if key == "OPENROUTER_API_KEY" else default),
            patch("scrapers.OpenAI", return_value=fake_client),
            patch("scrapers.get_openrouter_models", side_effect=RuntimeError("models unavailable")),
        ):
            with self.assertRaises(scrapers.ScraperError) as ctx:
                scrapers.get_ai_story()

        self.assertIn("No endpoints found for deepseek/deepseek-chat-v3-0324:free", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
