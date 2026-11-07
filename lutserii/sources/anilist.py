"""Розклад виходу серій аніме: AniList GraphQL (без ключа для читання)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..textutil import clean_text, norm_title

API = "https://graphql.anilist.co"
TIMEOUT = 20
MAX_PAGES = 10
MAX_STREAMING = 3
ALLOWED_FORMATS = {"TV", "TV_SHORT", "ONA"}
# Показуємо лише сервіси, які зазвичай доступні в Україні (решта лише збиває з пантелику)
STREAMING_SITES = {
    "crunchyroll", "netflix", "amazon prime video", "amazon", "disney plus", "disney+",
    "youtube", "bilibili tv",
}

QUERY_TEMPLATE = """
query ($start: Int, $end: Int, $page: Int) {
  Page(page: $page, perPage: 50) {
    pageInfo { hasNextPage }
    airingSchedules(airingAt_greater: $start, airingAt_lesser: $end, sort: TIME) {
      airingAt
      episode
      media {
        id
        idMal
        siteUrl
        format
        countryOfOrigin
        isAdult
        popularity
        genres
        description(asHtml: false)
        coverImage { extraLarge large }
        title { romaji english native }
        __LINKS__
      }
    }
  }
}
"""

LINKS_FIELDS = "externalLinks { site url type }"


def build_query(with_links: bool = True) -> str:
    return QUERY_TEMPLATE.replace("__LINKS__", LINKS_FIELDS if with_links else "")


@dataclass(frozen=True)
class Episode:
    airing_at: int  # unix-час UTC
    episode: int
    title: str
    popularity: int
    url: str
    media_id: int = 0
    description: str = ""  # англійською, як на AniList
    genres: tuple[str, ...] = ()
    cover: str | None = None
    streaming: tuple[tuple[str, str], ...] = ()  # (сайт, посилання) офіційних стрімінгів
    ua_title: str | None = None  # українська назва з titles_ua.json
    mal_id: int = 0  # номер на MyAnimeList (запасний опис)
    alt_titles: tuple[str, ...] = ()  # інші назви (ромадзі, англійська) для пошуку українського перекладу

    @property
    def display_title(self) -> str:
        """Українська назва з оригіналом у дужках або лише оригінал."""
        return f"{self.ua_title} ({self.title})" if self.ua_title else self.title


def _streaming(media: dict) -> tuple[tuple[str, str], ...]:
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for link in media.get("externalLinks") or []:
        if not isinstance(link, dict) or link.get("type") != "STREAMING":
            continue
        site = str(link.get("site") or "").strip()
        url = str(link.get("url") or "").strip()
        if (
            not site
            or site.casefold() not in STREAMING_SITES
            or not url.startswith("https://")
            or site.casefold() in seen
        ):
            continue
        seen.add(site.casefold())
        out.append((site, url))
    return tuple(out[:MAX_STREAMING])


def parse_episode(item: dict) -> Episode | None:
    """Повертає серію або None, якщо її треба відкинути (18+, не з Японії тощо)."""
    media = item.get("media")
    if not media:
        return None
    if media.get("isAdult") or media.get("countryOfOrigin") != "JP":
        return None
    if media.get("format") not in ALLOWED_FORMATS:
        return None
    titles = media.get("title") or {}
    title = titles.get("english") or titles.get("romaji") or titles.get("native")
    if not title:
        return None
    cover_info = media.get("coverImage") or {}
    return Episode(
        airing_at=int(item["airingAt"]),
        episode=int(item["episode"]),
        title=str(title),
        popularity=int(media.get("popularity") or 0),
        url=str(media.get("siteUrl") or ""),
        media_id=int(media.get("id") or 0),
        mal_id=int(media.get("idMal") or 0),
        description=clean_text(media.get("description")),
        genres=tuple(g for g in media.get("genres") or [] if isinstance(g, str)),
        cover=cover_info.get("extraLarge") or cover_info.get("large") or None,
        streaming=_streaming(media),
        alt_titles=tuple(
            str(t) for t in (titles.get("romaji"), titles.get("english")) if t and str(t) != title
        ),
    )


def fetch_airing(session: Any, start_ts: int, end_ts: int) -> list[Episode]:
    """Серії, що виходять між start_ts і end_ts (unix-час).

    Якщо AniList відхилить запит із посиланнями на стрімінги (схема могла змінитися),
    повторюємо без них: розклад важливіший за «Де дивитись».
    """
    episodes: list[Episode] = []
    with_links = True
    page = 1
    while page <= MAX_PAGES:
        resp = session.post(
            API,
            json={
                "query": build_query(with_links),
                "variables": {"start": start_ts, "end": end_ts, "page": page},
            },
            timeout=TIMEOUT,
        )
        if resp.status_code == 429:
            raise RuntimeError("AniList: забагато запитів, спробуємо наступного разу")
        try:
            payload = resp.json()
        except ValueError:
            payload = {}
        errors = payload.get("errors")
        if (errors or resp.status_code >= 400) and with_links:
            with_links = False
            continue
        if errors:
            raise RuntimeError(f"AniList: {errors[0].get('message', 'помилка')}")
        resp.raise_for_status()
        page_data = payload["data"]["Page"]
        for item in page_data["airingSchedules"]:
            parsed = parse_episode(item)
            if parsed:
                episodes.append(parsed)
        if not page_data["pageInfo"]["hasNextPage"]:
            break
        page += 1
    return episodes


SEARCH_QUERY = """
query ($s: String) {
  Page(perPage: 5) {
    media(search: $s, type: ANIME, sort: POPULARITY_DESC) {
      id
      idMal
      siteUrl
      isAdult
      synonyms
      popularity
      genres
      description(asHtml: false)
      title { romaji english native }
      coverImage { extraLarge large }
    }
  }
}
"""


@dataclass(frozen=True)
class AnimeInfo:
    """Аніме, знайдене за назвою: обкладинка й дані для картки."""

    id: int
    cover: str | None
    popularity: int = 0
    genres: tuple[str, ...] = ()
    description: str = ""
    url: str = ""
    title: str = ""
    mal_id: int = 0
    kind: str = "anime"  # anime | series


def pick_media(payload: Any, wanted: str) -> AnimeInfo | None:
    """Аніме, чия назва збігається з шуканою. Сумнівні збіги відкидаємо."""
    target = norm_title(wanted)
    if len(target) < 4 or not isinstance(payload, dict):
        return None
    page = ((payload.get("data") or {}).get("Page")) or {}
    for media in page.get("media") or []:
        if not isinstance(media, dict) or media.get("isAdult"):
            continue
        titles = media.get("title") or {}
        names = [titles.get("romaji"), titles.get("english"), titles.get("native")]
        names += [n for n in media.get("synonyms") or [] if isinstance(n, str)]
        for name in names:
            norm = norm_title(str(name or ""))
            if not norm:
                continue
            short, long_ = sorted((norm, target), key=len)
            # точний збіг або продовження назви («… 2nd Season»), але не випадкова схожість
            if norm == target or (len(short) >= 8 and long_.startswith(short)):
                cover = media.get("coverImage") or {}
                return AnimeInfo(
                    id=int(media.get("id") or 0),
                    mal_id=int(media.get("idMal") or 0),
                    cover=cover.get("extraLarge") or cover.get("large") or None,
                    popularity=int(media.get("popularity") or 0),
                    genres=tuple(g for g in media.get("genres") or [] if isinstance(g, str)),
                    description=clean_text(media.get("description")),
                    url=str(media.get("siteUrl") or ""),
                    title=str(titles.get("english") or titles.get("romaji") or ""),
                )
    return None


def pick_cover(payload: Any, wanted: str) -> str | None:
    info = pick_media(payload, wanted)
    return info.cover if info else None


def search_media(session: Any, title: str) -> AnimeInfo | None:
    resp = session.post(
        API, json={"query": SEARCH_QUERY, "variables": {"s": title}}, timeout=TIMEOUT
    )
    resp.raise_for_status()
    return pick_media(resp.json(), title)


def search_cover(session: Any, title: str) -> str | None:
    info = search_media(session, title)
    return info.cover if info else None
