"""SQLite history storage for duplicate prevention and dashboard history."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any


def _normalize_text(text: str) -> str:
    return " ".join((text or "").split()).strip().lower()


def _fingerprint(text: str) -> str:
    return hashlib.sha256(_normalize_text(text).encode("utf-8")).hexdigest()


def _connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def init_db(db_path: str = "history.db") -> None:
    with _connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                video_filename TEXT NOT NULL,
                content_fingerprint TEXT NOT NULL UNIQUE,
                content_text TEXT NOT NULL
            )
            """
        )
        connection.commit()


def has_content_fingerprint(text: str, db_path: str = "history.db") -> bool:
    digest = _fingerprint(text)
    with _connect(db_path) as connection:
        row = connection.execute(
            "SELECT id FROM history WHERE content_fingerprint = ? LIMIT 1",
            (digest,),
        ).fetchone()
        return row is not None


def log_history_entry(
    created_at: str,
    source: str,
    title: str,
    video_filename: str,
    content_text: str,
    db_path: str = "history.db",
) -> None:
    digest = _fingerprint(content_text)
    with _connect(db_path) as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO history (
                created_at,
                source,
                title,
                video_filename,
                content_fingerprint,
                content_text
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (created_at, source, title, video_filename, digest, content_text),
        )
        connection.commit()


def fetch_recent_history(limit: int = 50, db_path: str = "history.db") -> list[dict[str, Any]]:
    with _connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT created_at, source, title, video_filename
            FROM history
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]
