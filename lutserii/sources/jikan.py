"""Запасний опис аніме: синопсис із MyAnimeList через Jikan (без ключа).

Шукаємо лише за точним номером MyAnimeList, який віддає AniList, тож переплутати тайтл неможливо.
"""

from __future__ import annotations

from typing import Any

from ..textutil import clean_text

API = "https://api.jikan.moe/v4/anime/{}"
TIMEOUT = 20


def parse_synopsis(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    data = payload.get("data")
    if not isinstance(data, dict) or "Rx" in str(data.get("rating") or ""):  # дорослий вміст не беремо
        return ""
    return clean_text(str(data.get("synopsis") or ""))


def fetch_synopsis(session: Any, mal_id: int) -> str:
    resp = session.get(API.format(int(mal_id)), timeout=TIMEOUT)
    if resp.status_code == 404:
        return ""
    resp.raise_for_status()
    return parse_synopsis(resp.json())
