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


def _install_bot_import_stubs() -> None:
    if "dotenv" not in sys.modules:
        dotenv = types.ModuleType("dotenv")
        dotenv.load_dotenv = lambda: None
        sys.modules["dotenv"] = dotenv

    if "schedule" not in sys.modules:
        schedule = types.ModuleType("schedule")
        schedule.every = lambda: None
        schedule.run_pending = lambda: None
        schedule.ScheduleValueError = ValueError
        sys.modules["schedule"] = schedule

    if "scrapers" not in sys.modules:
        scrapers = types.ModuleType("scrapers")
        scrapers.get_ai_story = lambda: "ai"
        scrapers.get_reddit_story = lambda: "reddit"
        scrapers.get_wiki_fact = lambda: "wiki"
        scrapers.has_reddit_credentials = lambda: True
        sys.modules["scrapers"] = scrapers


_install_bot_import_stubs()
bot = importlib.import_module("bot")


class TestBotHelpers(unittest.TestCase):
    def test_choose_scraper_raises_when_only_reddit_without_credentials(self) -> None:
        with (
            patch("bot.get_config", return_value={"scrapers": {"selection_pool": ["reddit"]}}),
            patch("scrapers.has_reddit_credentials", return_value=False),
            patch("bot.random.shuffle", side_effect=lambda items: items.__setitem__(slice(None), ["reddit"])),
        ):
            with self.assertRaises(RuntimeError):
                bot._choose_scraper()


if __name__ == "__main__":
    unittest.main()
