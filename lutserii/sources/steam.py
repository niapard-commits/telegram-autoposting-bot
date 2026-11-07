"""Опис гри, жанри та скріншоти зі Steam Store API (без ключа)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..textutil import base_title, clean_text, norm_title

API = "https://store.steampowered.com/api/appdetails"
SEARCH_API = "https://store.steampowered.com/api/storesearch/"
TIMEOUT = 20


@dataclass(frozen=True)
class AppInfo:
    description: str
    genres: tuple[str, ...]
    screenshots: tuple[str, ...]
    price_final: str = ""  # ціна в Steam України, як її показує магазин («249₴»)
    price_initial: str = ""  # ціна без знижки; порожньо, якщо знижки немає
    trailer: str = ""  # посилання на mp4-трейлер (https) або порожньо


def parse_app(payload: Any, app_id: str) -> AppInfo | None:
    entry = payload.get(str(app_id)) if isinstance(payload, dict) else None
    if not isinstance(entry, dict) or not entry.get("success"):
        return None
    data = entry.get("data") or {}
    genres = tuple(
        g
        for g in (
            clean_text(item.get("description"))
            for item in data.get("genres") or []
            if isinstance(item, dict)
        )
        if g
    )
    screenshots = tuple(
        url
        for url in (
            item.get("path_full")
            for item in data.get("screenshots") or []
            if isinstance(item, dict)
        )
        if isinstance(url, str) and url.startswith("https://")
    )
    price = data.get("price_overview") if isinstance(data.get("price_overview"), dict) else {}
    final = clean_text(price.get("final_formatted"))
    initial = clean_text(price.get("initial_formatted")) if price.get("discount_percent") else ""
    return AppInfo(
        clean_text(data.get("short_description")), genres, screenshots, final, initial,
        _trailer(data.get("movies")),
    )


def _trailer(movies: Any) -> str:
    """mp4 першого ролика: якість 480 (менше важить), інакше максимальна; завжди https."""
    for movie in movies or []:
        mp4 = movie.get("mp4") if isinstance(movie, dict) else None
        if not isinstance(mp4, dict):
            continue
        url = mp4.get("480") or mp4.get("max")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            return "https://" + url.split("://", 1)[1]
    return ""


def header_image(app_id: str) -> str:
    return f"https://cdn.cloudflare.steamstatic.com/steam/apps/{app_id}/header.jpg"


def pick_app_id(payload: Any, title: str) -> str | None:
    """Із результатів пошуку Steam бере застосунок, назва якого збігається з базовою назвою."""
    wanted = norm_title(base_title(title))
    if not wanted or not isinstance(payload, dict):
        return None
    for item in payload.get("items") or []:
        if not isinstance(item, dict) or item.get("type") != "app" or not item.get("id"):
            continue
        if norm_title(base_title(str(item.get("name", "")))) == wanted:
            return str(item["id"])
    return None


def search_app_id(session: Any, title: str) -> str | None:
    """Резервний пошук, коли CheapShark не дав steamAppID (наприклад, для видань на кшталт Ultimate)."""
    resp = session.get(
        SEARCH_API,
        params={"term": base_title(title), "l": "ukrainian", "cc": "US"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return pick_app_id(resp.json(), title)


def fetch_app(session: Any, app_id: str) -> AppInfo | None:
    """Українською, якщо у Steam є переклад; інакше Steam віддає англійську."""
    resp = session.get(
        API, params={"appids": app_id, "l": "ukrainian", "cc": "ua"}, timeout=TIMEOUT
    )
    resp.raise_for_status()
    return parse_app(resp.json(), app_id)
