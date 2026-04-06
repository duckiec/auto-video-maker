"""Configuration loader and saver backed by config.json."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from json import JSONDecodeError
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "scheduler": {
        "times": ["08:00", "17:00"],
        "extra_times": [],
        "run_on_start": False,
        "recovery_sleep_seconds": 5,
    },
    "paths": {
        "output_dir": "output",
        "cookies_dir": "cookies",
        "background_video": "assets/gameplay.mp4",
        "history_db": "history.db",
    },
    "scrapers": {
        "selection_pool": ["reddit", "wiki", "ai"],
        "reddit": {
            "subreddits": ["AskReddit", "AmItheAsshole"],
            "max_words": 200,
            "post_limit": 50,
            "time_filter": "day",
        },
        "wiki": {"max_words": 120, "min_words": 15},
        "ai": {
            "target_words": 100,
            "max_words": 140,
            "min_words": 60,
            "model": "deepseek/deepseek-chat-v3-0324:free",
        },
    },
    "audio": {"voice": "en-US-ChristopherNeural", "rate": "+0%", "volume": "+0%"},
    "video": {
        "whisper_model": "base",
        "subtitle": {"min_words": 1, "max_words": 3, "font_size": 84, "stroke_width": 6},
        "output": {"width": 1080, "height": 1920, "fps": 30},
    },
    "uploader": {
        "platform": "random",
        "headless": True,
        "timeout_ms": 120000,
        "youtube_state_file": "youtube_state.json",
        "tiktok_state_file": "tiktok_state.json",
        "base_tags": ["#shorts", "#story", "#viral"],
    },
    "api": {
        "openrouter_base_url": "https://openrouter.ai/api/v1",
        "openrouter_referer": "https://local.video-factory",
        "openrouter_title": "Auto Video Maker",
        "reddit_user_agent": "video-factory/1.0 (by u/auto-video-bot)",
        "wiki_user_agent": "video-factory/1.0 (contact: local)",
    },
}


def _config_path() -> Path:
    env_path = os.getenv("CONFIG_PATH")
    if env_path:
        return Path(env_path).expanduser()
    return Path(__file__).resolve().parent.parent / "config.json"


def _write_config(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    temp_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    temp_path.replace(path)


def _merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def get_config() -> dict[str, Any]:
    path = _config_path()
    if not path.exists():
        _write_config(path, DEFAULT_CONFIG)
        return deepcopy(DEFAULT_CONFIG)

    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (JSONDecodeError, OSError):
        _write_config(path, DEFAULT_CONFIG)
        return deepcopy(DEFAULT_CONFIG)

    if not isinstance(loaded, dict):
        _write_config(path, DEFAULT_CONFIG)
        return deepcopy(DEFAULT_CONFIG)

    return _merge_dict(DEFAULT_CONFIG, loaded)


def save_config(config: dict[str, Any]) -> None:
    path = _config_path()
    merged = _merge_dict(DEFAULT_CONFIG, config)
    _write_config(path, merged)
