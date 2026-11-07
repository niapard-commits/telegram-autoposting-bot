"""Серіали з TMDB: український опис, постер, жанри. Потрібен ключ (TMDB_API_KEY), без нього не використовується.

This product uses the TMDB API but is not endorsed or certified by TMDB.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..textutil import clean_text, norm_title
from .anilist import AnimeInfo

API = "https://api.themoviedb.org/3"
IMAGE = "https://image.tmdb.org/t/p/w780"
PAGE = "https://www.themoviedb.org/tv/{}"
TIMEOUT = 20
POPULARITY_SCALE = 400  # популярність TMDB (десятки-сотні) -> шкала AniList; 100 і більше = 40000
# Жанри TMDB -> англійські назви, які вміє перекладати posts.ua_genres
GENRES = {
    10759: ("Action", "Adventure"), 35: ("Comedy",), 80: ("Crime",), 18: ("Drama",),
    10751: ("Family",), 9648: ("Mystery",), 10765: ("Sci-Fi", "Fantasy"), 10768: ("War",),
    37: ("Western",),
}


def _auth(key: str) -> tuple[dict[str, str], dict[str, str]]:
    """Довгий токен читання (v4, починається з «eyJ») шлемо заголовком, короткий ключ v3 параметром."""
    if key.startswith("eyJ"):
        return {"Authorization": f"Bearer {key}"}, {}
    return {}, {"api_key": key}


def _get(session: Any, key: str, path: str, params: dict[str, str]) -> Any:
    headers, extra = _auth(key)
    resp = session.get(f"{API}{path}", params={**extra, **params}, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def pick_tv(payload: Any, wanted: str) -> AnimeInfo | None:
    """Серіал, чия назва (місцева чи оригінальна) точно збігається з шуканою."""
    target = norm_title(wanted)
    if len(target) < 3 or not isinstance(payload, dict):
        return None
    for item in payload.get("results") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        names = {norm_title(str(item.get(k) or "")) for k in ("name", "original_name")}
        if target not in names:
            continue
        poster = item.get("poster_path")
        genres: list[str] = []
        for gid in item.get("genre_ids") or []:
            genres += [g for g in GENRES.get(gid, ()) if g not in genres]
        return AnimeInfo(
            id=int(item["id"]),
            cover=f"{IMAGE}{poster}" if isinstance(poster, str) and poster.startswith("/") else None,
            popularity=int(min(float(item.get("popularity") or 0), 250) * POPULARITY_SCALE),
            genres=tuple(genres),
            description=clean_text(item.get("overview")),
            url=PAGE.format(int(item["id"])),
            title=str(item.get("name") or item.get("original_name") or ""),
            kind="series",
        )
    return None


def find_series(session: Any, key: str, *titles: str) -> AnimeInfo | None:
    """Шукає за кожною назвою по черзі (оригінал, українська); якщо українського опису немає, бере англійський."""
    seen: set[str] = set()
    for title in titles:
        if not title or title.casefold() in seen:
            continue
        seen.add(title.casefold())
        info = pick_tv(_get(session, key, "/search/tv", {"query": title, "language": "uk-UA"}), title)
        if info is None:
            continue
        if not info.description:
            detail = _get(session, key, f"/tv/{info.id}", {"language": "en-US"})
            overview = clean_text(detail.get("overview")) if isinstance(detail, dict) else ""
            info = AnimeInfo(**{**info.__dict__, "description": overview})
        return info
    return None


def check_key(session: Any, key: str) -> str:
    """Чи працює ключ: шукаємо відомий серіал і повертаємо його назву."""
    payload = _get(session, key, "/search/tv", {"query": "Breaking Bad", "language": "uk-UA"})
    results = payload.get("results") if isinstance(payload, dict) else None
    return str((results or [{}])[0].get("name") or "") if results else ""


# Не показуємо: ток-шоу, новини, реаліті, мильні опери, документалістику
SKIP_GENRES = {10767, 10763, 10764, 10766, 99}


@dataclass(frozen=True)
class AiringShow:
    """Серіал, у якого щойно вийшла серія."""

    id: int
    name: str  # українська назва (або оригінальна, якщо перекладу немає)
    original_name: str
    popularity: float
    overview: str
    poster: str | None
    genres: tuple[str, ...]
    season: int
    episode: int
    episode_name: str
    air_date: str  # РРРР-ММ-ДД
    network: str
    url: str


def fetch_airing_today(session: Any, key: str, pages: int = 2) -> list[dict]:
    """Серіали, у яких сьогодні виходить серія (TMDB сортує за популярністю)."""
    out: list[dict] = []
    for page in range(1, pages + 1):
        payload = _get(session, key, "/tv/airing_today", {"language": "uk-UA", "page": str(page)})
        results = payload.get("results") if isinstance(payload, dict) else None
        out += [r for r in results or [] if isinstance(r, dict)]
    return out


LANGUAGES = {"en", "uk", "ko"}  # оригінальна мова: англомовні, українські й корейські серіали


def is_candidate(item: dict, min_popularity: float) -> bool:
    return (
        bool(item.get("id"))
        and item.get("original_language") in LANGUAGES
        and float(item.get("popularity") or 0) >= min_popularity
        and not SKIP_GENRES & set(item.get("genre_ids") or [])
    )


def fetch_airing_show(session: Any, key: str, show_id: int, today: str = "") -> AiringShow | None:
    """Подробиці серіалу й серії, що виходить чи щойно вийшла; None, якщо даних замало.
    Якщо «наступна» серія має дату не пізніше сьогодні, беремо її (дані «останньої» в TMDB запізнюються)."""
    detail = _get(session, key, f"/tv/{int(show_id)}", {"language": "uk-UA"})
    if not isinstance(detail, dict):
        return None
    last = detail.get("last_episode_to_air")
    upcoming = detail.get("next_episode_to_air")
    if isinstance(upcoming, dict) and today and str(upcoming.get("air_date") or "9999") <= today:
        last = upcoming
    if not isinstance(last, dict) or not last.get("air_date") or not last.get("episode_number"):
        return None
    overview = clean_text(detail.get("overview"))
    if not overview:
        english = _get(session, key, f"/tv/{int(show_id)}", {"language": "en-US"})
        overview = clean_text(english.get("overview")) if isinstance(english, dict) else ""
    genres: list[str] = []
    for genre in detail.get("genres") or []:
        if isinstance(genre, dict):
            genres += [g for g in GENRES.get(genre.get("id"), ()) if g not in genres]
    poster = detail.get("poster_path")
    networks = detail.get("networks") or []
    return AiringShow(
        id=int(show_id),
        name=str(detail.get("name") or detail.get("original_name") or ""),
        original_name=str(detail.get("original_name") or ""),
        popularity=float(detail.get("popularity") or 0),
        overview=overview,
        poster=f"{IMAGE}{poster}" if isinstance(poster, str) and poster.startswith("/") else None,
        genres=tuple(genres),
        season=int(last.get("season_number") or 1),
        episode=int(last["episode_number"]),
        episode_name=clean_text(last.get("name")),
        air_date=str(last["air_date"]),
        network=str((networks[0] or {}).get("name") or "") if networks and isinstance(networks[0], dict) else "",
        url=PAGE.format(int(show_id)),
    )
