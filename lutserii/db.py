"""Проста SQLite-база: що вже опубліковано (щоб не дублювати пости)."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


class StateDB:
    def __init__(self, path: str = "data/state.db") -> None:
        if path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS posted ("
            "key TEXT PRIMARY KEY, kind TEXT NOT NULL, posted_at TEXT NOT NULL)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        self.conn.commit()

    def get_cache(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM cache WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_cache(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO cache (key, value) VALUES (?, ?)", (key, value))
        self.conn.commit()

    def cache_items(self, prefix: str) -> dict[str, str]:
        """Усі записи кешу, ключі яких починаються з prefix (без самого префікса в ключах результату)."""
        rows = self.conn.execute(
            "SELECT key, value FROM cache WHERE substr(key, 1, ?) = ?", (len(prefix), prefix)
        ).fetchall()
        return {key[len(prefix):]: value for key, value in rows}

    def was_posted(
        self, key: str, within_days: int | None = None, now: datetime | None = None
    ) -> bool:
        """Чи публікували ключ. Із within_days рахуємо лише недавні публікації."""
        row = self.conn.execute(
            "SELECT posted_at FROM posted WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return False
        if within_days is None:
            return True
        posted = datetime.fromisoformat(row[0])
        return (now or _now()) - posted < timedelta(days=within_days)

    def mark_posted(self, key: str, kind: str, now: datetime | None = None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO posted (key, kind, posted_at) VALUES (?, ?, ?)",
            (key, kind, _iso(now or _now())),
        )
        self.conn.commit()

    def count_since(self, kind: str, since: datetime) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM posted WHERE kind = ? AND posted_at >= ?",
            (kind, _iso(since)),
        ).fetchone()
        return int(row[0])

    def count_kind(self, kind: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM posted WHERE kind = ?", (kind,)
        ).fetchone()
        return int(row[0])

    def close(self) -> None:
        self.conn.close()
