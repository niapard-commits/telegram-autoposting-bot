"""Серіали: постер, опис, жанри з TVMaze (без ключа). Назву шукаємо лише за точним збігом."""

from __future__ import annotations

from typing import Any

from ..textutil import clean_text, norm_title
from .anilist import AnimeInfo

API = "https://api.tvmaze.com/singlesearch/shows"
TIMEOUT = 20
WEIGHT_SCALE = 500  # «вага» TVMaze (0-100) -> шкала популярності AniList, щоб серіали ранжувалися нарівні


def pick_show(payload: Any, wanted: str) -> AnimeInfo | None:
    """Серіал, чия назва збігається з шуканою; сумнівні збіги відкидаємо."""
    target = norm_title(wanted)
    if len(target) < 3 or not isinstance(payload, dict) or not payload.get("id"):
        return None
    if norm_title(str(payload.get("name") or "")) != target:
        return None  # лише точна назва: інакше легко переплутати з іншим серіалом
    image = payload.get("image") or {}
    cover = image.get("original") or image.get("medium") or None
    url = str(payload.get("url") or "")
    return AnimeInfo(
        id=int(payload["id"]),
        cover=cover if isinstance(cover, str) and cover.startswith("https://") else (
            cover.replace("http://", "https://", 1) if isinstance(cover, str) else None
        ),
        popularity=int(float((payload.get("weight") or 0)) * WEIGHT_SCALE),
        genres=tuple(g for g in payload.get("genres") or [] if isinstance(g, str)),
        description=clean_text(payload.get("summary")),
        url=url if url.startswith("https://") else url.replace("http://", "https://", 1),
        title=str(payload.get("name") or ""),
        kind="series",
    )


def search_show(session: Any, title: str) -> AnimeInfo | None:
    resp = session.get(API, params={"q": title}, timeout=TIMEOUT)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return pick_show(resp.json(), title)
