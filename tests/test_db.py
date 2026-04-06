from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, "/home/runner/work/auto-video-maker/auto-video-maker/src")

import db


class TestDb(unittest.TestCase):
    def test_fingerprint_ignores_case_and_whitespace(self) -> None:
        a = db._fingerprint("  Hello   World ")
        b = db._fingerprint("hello world")
        self.assertEqual(a, b)

    def test_log_history_deduplicates_by_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "history.db")
            db.init_db(db_path)

            db.log_history_entry(
                created_at="2026-01-01T00:00:00Z",
                source="reddit",
                title="t1",
                video_filename="v1.mp4",
                content_text="same text",
                db_path=db_path,
            )
            db.log_history_entry(
                created_at="2026-01-01T00:01:00Z",
                source="wiki",
                title="t2",
                video_filename="v2.mp4",
                content_text="  SAME   text  ",
                db_path=db_path,
            )

            rows = db.fetch_recent_history(limit=10, db_path=db_path)
            self.assertEqual(len(rows), 1)
            self.assertTrue(db.has_content_fingerprint("same text", db_path=db_path))

    def test_fetch_recent_history_returns_descending_order_and_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "history.db")
            db.init_db(db_path)

            for idx in range(5):
                db.log_history_entry(
                    created_at=f"2026-01-01T00:00:0{idx}Z",
                    source="ai",
                    title=f"title-{idx}",
                    video_filename=f"file-{idx}.mp4",
                    content_text=f"unique text {idx}",
                    db_path=db_path,
                )

            rows = db.fetch_recent_history(limit=3, db_path=db_path)
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0]["title"], "title-4")
            self.assertEqual(rows[1]["title"], "title-3")
            self.assertEqual(rows[2]["title"], "title-2")


if __name__ == "__main__":
    unittest.main()
