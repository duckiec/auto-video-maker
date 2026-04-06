from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

SRC_PATH = str(Path(__file__).resolve().parents[1] / "src")
sys.path.insert(0, SRC_PATH)

import config_store


class TestConfigStore(unittest.TestCase):
    def test_get_config_creates_default_when_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            with patch.dict(os.environ, {"CONFIG_PATH": str(config_path)}, clear=False):
                config = config_store.get_config()

            self.assertTrue(config_path.exists())
            self.assertEqual(config, config_store.DEFAULT_CONFIG)
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(loaded, config_store.DEFAULT_CONFIG)

    def test_get_config_merges_nested_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            override = {
                "audio": {"voice": "custom-voice"},
                "video": {"subtitle": {"font_size": 42}},
            }
            config_path.write_text(json.dumps(override), encoding="utf-8")

            with patch.dict(os.environ, {"CONFIG_PATH": str(config_path)}, clear=False):
                merged = config_store.get_config()

            self.assertEqual(merged["audio"]["voice"], "custom-voice")
            self.assertEqual(merged["video"]["subtitle"]["font_size"], 42)
            self.assertEqual(
                merged["video"]["subtitle"]["stroke_width"],
                config_store.DEFAULT_CONFIG["video"]["subtitle"]["stroke_width"],
            )

    def test_save_config_merges_defaults_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            partial = {"uploader": {"platform": "youtube"}}

            with patch.dict(os.environ, {"CONFIG_PATH": str(config_path)}, clear=False):
                config_store.save_config(partial)

            saved = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["uploader"]["platform"], "youtube")
            self.assertIn("paths", saved)
            self.assertIn("scrapers", saved)


if __name__ == "__main__":
    unittest.main()
