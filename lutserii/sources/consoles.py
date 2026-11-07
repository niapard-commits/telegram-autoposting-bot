"""Консолі: знижки українського PS Store / Microsoft Store (RSS PSprices) та оголошення офіційних блогів
(PlayStation Plus, Xbox Game Pass, Free Play Days)."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

PSPRICES_RSS = "https://psprices.com/region-ua/rss/discounts/"
PS_BLOG_FEED = "https://blog.playstation.com/category/ps-plus/feed/"
XBOX_FEEDS = (
    "https://news.xbox.com/en-us/feed/",
    "https://news.xbox.com/en-us/xbox-game-pass/feed/",
)
TIMEOUT = 25
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)

_ITEM = re.compile(r"<item>(.*?)</item>", re.S)
_TAG = re.compile(r"<(?:!\[CDATA\[)?")


def _text(item: str, tag: str) -> str:
    m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", item, re.S)
    if not m:
        return ""
    value = re.sub(r"<!\[CDATA\[|\]\]>", "", m.group(1))
    return html.unescape(value).strip()


def _image(item: str) -> str | None:
    for pattern in (r'<media:thumbnail[^>]+url="([^"]+)"', r'<media:content[^>]+url="([^"]+)"',
                    r'<enclosure[^>]+url="([^"]+)"', r"<img[^>]+src=[\"']([^\"']+)[\"']"):
        m = re.search(pattern, html.unescape(item))
        if m and m.group(1).startswith("https://"):
            return m.group(1)
    return None


def parse_feed(xml: str) -> list[dict]:
    """Записи RSS: title, link, published (UTC), image."""
    out = []
    for item in _ITEM.findall(xml or ""):
        title, link = _text(item, "title"), _text(item, "link")
        if not title or not link:
            continue
        try:
            published = parsedate_to_datetime(_text(item, "pubDate")).astimezone(timezone.utc)
        except (TypeError, ValueError):
            published = None
        out.append({"title": title, "link": link, "published": published, "image": _image(item)})
    return out


# ---------- знижки PSprices (Україна) ----------

@dataclass(frozen=True)
class ConsoleDeal:
    game_id: str
    title: str
    platforms: tuple[str, ...]
    discount: int
    price_old: float
    price_new: float
    ps_plus_only: bool  # безкоштовно лише за передплатою PS Plus (це окрема рубрика, не знижка)
    url: str
    image: str | None

    @property
    def family(self) -> str:
        return "Xbox" if any(p.upper().startswith("X") for p in self.platforms) else "PlayStation"

    @property
    def store(self) -> str:
        return "Microsoft Store" if self.family == "Xbox" else "PS Store"

    @property
    def free(self) -> bool:
        return self.price_new == 0 and not self.ps_plus_only


_DEAL_TITLE = re.compile(
    r"^\[(?P<plat>[^\]]+)\]\s+(?P<name>.+?)\s+[−\-–](?P<pct>\d+)%\s+"
    r"\((?P<old>[^→)]+?)\s*→\s*(?P<new>[^/)]+?)(?:\s*/\s*(?P<plus>[^)]+?)\s*PS\+)?\)\s*$"
)


def _uah(raw: str) -> float | None:
    cleaned = re.sub(r"[^\d,.]", "", raw or "").replace(",", ".")
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def parse_deals(xml: str) -> list[ConsoleDeal]:
    deals = []
    for entry in parse_feed(xml):
        m = _DEAL_TITLE.match(" ".join(entry["title"].split()))
        game = re.search(r"/game/(\d+)/", entry["link"])
        if not m or not game:
            continue
        old, new = _uah(m["old"]), _uah(m["new"])
        if old is None or new is None:
            continue
        url = entry["link"].split("&date=")[0].split("?")[0]
        deals.append(ConsoleDeal(
            game_id=game.group(1), title=re.sub(r"[　\s]+", " ", m["name"]).strip(),
            platforms=tuple(m["plat"].split()), discount=int(m["pct"]), price_old=old, price_new=new,
            ps_plus_only=m["plus"] is not None, url=url, image=entry["image"],
        ))
    return deals


def fetch_deals(session: Any) -> list[ConsoleDeal]:
    resp = session.get(PSPRICES_RSS, timeout=TIMEOUT)
    if resp.status_code != 200:
        raise RuntimeError(f"PSprices: HTTP {resp.status_code}")
    return parse_deals(resp.text)


# ---------- оголошення блогів ----------

@dataclass(frozen=True)
class Announcement:
    kind: str  # psplus | gamepass | freeplay
    label: str  # місяць для PS Plus, інакше порожньо
    games: tuple[str, ...]
    url: str
    published: datetime | None
    image: str | None


def _split_games(raw: str) -> tuple[str, ...]:
    raw = re.sub(r"\s+(?:and|&)\s+more\.?$", "", raw.strip(), flags=re.I)
    raw = re.sub(r",?\s+and\s+more\.?$", "", raw, flags=re.I)
    parts = [p.strip() for p in re.split(r",\s*(?:and\s+)?|\s+and\s+(?=[A-Z0-9])", raw) if p.strip()]
    return tuple(dict.fromkeys(p for p in parts if len(p) > 1))


def parse_announcements(entries: list[dict]) -> list[Announcement]:
    out = []
    for e in entries:
        title = " ".join(e["title"].split())
        m = re.match(r"^PlayStation Plus Monthly Games for (\w+):\s+(.+)$", title)
        if m:
            kind, label, rest = "psplus", m.group(1), m.group(2)
        else:
            m = re.match(r"^Coming to (?:XBOX|Xbox) Game Pass:?\s+(.+)$", title)
            if m:
                kind, label, rest = "gamepass", "", m.group(1)
            else:
                m = re.match(r"^Free Play Days\s*[–\-:]\s*(.+)$", title)
                if not m:
                    continue
                kind, label, rest = "freeplay", "", m.group(1)
        games = _split_games(rest)
        if games:
            out.append(Announcement(kind, label, games, e["link"], e["published"], e["image"]))
    return out


def fetch_announcements(session: Any) -> list[Announcement]:
    entries: list[dict] = []
    for url in (PS_BLOG_FEED, *XBOX_FEEDS):
        try:
            resp = session.get(url, timeout=TIMEOUT)
            if resp.status_code == 200:
                entries += parse_feed(resp.text)
        except Exception:  # один блог не відповів: решта працює
            continue
    seen: set[str] = set()
    unique = [e for e in entries if not (e["link"] in seen or seen.add(e["link"]))]
    return parse_announcements(unique)
