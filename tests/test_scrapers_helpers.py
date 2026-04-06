from __future__ import annotations

import importlib
import sys
import types
import unittest
from unittest.mock import patch


SRC_PATH = "/home/runner/work/auto-video-maker/auto-video-maker/src"
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
            patch("scrapers.random.choice", return_value="wiki"),
        ):
            result = scrapers.get_random_content()

        self.assertEqual(result, "wiki text")


if __name__ == "__main__":
    unittest.main()
