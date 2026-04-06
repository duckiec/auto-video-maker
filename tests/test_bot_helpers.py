from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
import types
import unittest
from unittest.mock import patch


SRC_PATH = str(Path(__file__).resolve().parents[1] / "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)


def _load_bot_module() -> types.ModuleType:
    module_name = "bot_real_for_tests"
    spec = importlib.util.spec_from_file_location(
        module_name,
        str(Path(__file__).resolve().parents[1] / "src" / "bot.py"),
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class TestBotHelpers(unittest.TestCase):
    def test_choose_scraper_raises_when_only_reddit_without_credentials(self) -> None:
        dotenv = types.ModuleType("dotenv")
        dotenv.load_dotenv = lambda: None
        schedule = types.ModuleType("schedule")
        schedule.every = lambda: None
        schedule.run_pending = lambda: None
        schedule.ScheduleValueError = ValueError
        scrapers = types.ModuleType("scrapers")
        scrapers.get_ai_story = lambda: "ai"
        scrapers.get_reddit_story = lambda: "reddit"
        scrapers.get_wiki_fact = lambda: "wiki"
        scrapers.has_reddit_credentials = lambda: False

        with (
            patch.dict(
                sys.modules,
                {"dotenv": dotenv, "schedule": schedule, "scrapers": scrapers},
            ),
        ):
            bot = _load_bot_module()
            with (
                patch.object(
                    bot, "get_config", return_value={"scrapers": {"selection_pool": ["reddit"]}}
                ),
                patch.object(
                    bot.random,
                    "shuffle",
                    side_effect=lambda items: items.__setitem__(slice(None), ["reddit"]),
                ),
            ):
                with self.assertRaises(RuntimeError):
                    bot._choose_scraper()


if __name__ == "__main__":
    unittest.main()
