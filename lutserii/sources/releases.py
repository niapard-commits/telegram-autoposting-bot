"""Календар релізів ігор: таблиця «List of video games released in YYYY» у Вікіпедії (дата, платформи, тип).
Беремо лише помітні релізи на ПК, PlayStation і Xbox (Switch свідомо не показуємо)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from ..textutil import base_title, norm_title

RAW_URL = "https://en.wikipedia.org/w/index.php"
HEADERS = {"User-Agent": "lut-i-serii-bot/1.0 (https://t.me/lut_i_serii; release calendar reader)"}
TIMEOUT = 40
STEAM_SEARCH = "https://store.steampowered.com/search/results/"
STEAM_FEATURED = "https://store.steampowered.com/api/featuredcategories"
PLATFORMS = {"WIN": "ПК", "PS5": "PS5", "PS4": "PS4", "XBX/S": "Xbox Series", "XBO": "Xbox One"}
SKIP_TYPES = ("port", "dlc", "expansion", "update", "early access", "season")
_DATE = re.compile(r"^([A-Z][a-z]+)\s+(\d{1,2})$")
_REGION = re.compile(r"\((?:JP|NA|EU|AU|KR|CN|WW|PAL|Asia|China|Japan)[^)]*\)\s*$", re.I)
MONTHS = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"])}


@dataclass(frozen=True)
class Release:
    day: date
    title: str
    platforms: tuple[str, ...]  # підписи для постів: «ПК», «PS5», …
    kind: str  # Original, Remake, Remaster…
    notable: bool  # у назви є власна стаття у Вікіпедії

    @property
    def key(self) -> str:
        return f"{norm_title(base_title(self.title))}:{self.day.isoformat()}"


def _clean(cell: str) -> str:
    cell = re.sub(r"<ref[^>/]*>.*?</ref>|<ref[^>]*/>", "", cell, flags=re.S)
    cell = re.sub(r"<[^>]+>", "", cell)
    for _ in range(3):  # вкладені шаблони
        cell = re.sub(r"\{\{[^{}]*\}\}", "", cell)
    cell = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", cell)
    return cell.replace("''", "").strip()


_ATTR = re.compile(r'^(?:\s*\w+\s*=\s*"[^"]*"\s*)+\|(?!\|)')


def _rows(wikitext: str) -> list[list[str]]:
    rows: list[list[str]] = []
    cur: list[str] | None = None
    in_table = False
    for line in wikitext.splitlines():
        if line.startswith("{|"):
            in_table, cur = True, None
        elif line.startswith("|}"):
            in_table, cur = False, None
        elif not in_table:
            continue
        elif line.startswith("|-"):
            cur = []
            rows.append(cur)
        elif line.startswith("!"):
            continue
        elif line.startswith("|") and cur is not None:
            body = line[1:]
            body = _ATTR.sub("", body, count=1)
            cur.append(body.strip())
        elif cur:  # продовження комірки на наступному рядку
            cur[-1] += "\n" + line
    return [r for r in rows if r]


def parse_wikitext(text: str, year: int) -> list[Release]:
    out: list[Release] = []
    last_day: date | None = None
    for cells in _rows(text):
        first = _clean(cells[0])
        m = _DATE.match(first)
        if m and m.group(1) in MONTHS:
            try:
                last_day = date(year, MONTHS[m.group(1)], int(m.group(2)))
            except ValueError:
                continue
            rest = cells[1:]
        else:  # рядок без дати (rowspan): та сама дата, що в попередньому
            rest = cells
        if last_day is None or len(rest) < 3:
            continue
        raw_title, plat_cell = rest[0], rest[1]
        title = _clean(raw_title)
        kind = _clean(rest[2]) if len(rest) > 2 else ""
        if not title or _REGION.search(title) or any(s in kind.lower() for s in SKIP_TYPES):
            continue
        codes = [c.strip() for c in _clean(plat_cell).split(",")]
        platforms = tuple(PLATFORMS[c] for c in codes if c in PLATFORMS)
        if not platforms:  # лише Switch, мобільні тощо
            continue
        out.append(Release(last_day, title, platforms, kind, "[[" in raw_title))
    return out


def fetch(session: Any, year: int) -> list[Release]:
    resp = session.get(RAW_URL, params={"title": f"List_of_video_games_released_in_{year}", "action": "raw"},
                       headers=HEADERS, timeout=TIMEOUT)
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    return parse_wikitext(resp.text, year)


def steam_popular(session: Any) -> set[str]:
    """Назви (нормалізовані) з популярного у Steam: «Популярні новинки» та «Популярні майбутні». Сигнал, що гру чекають."""
    names: set[str] = set()
    for flt in ("popularnew", "popularupcoming"):
        resp = session.get(STEAM_SEARCH, params={"query": "", "start": 0, "count": 100, "filter": flt,
                                                 "cc": "UA", "l": "ukrainian", "json": 1}, timeout=TIMEOUT)
        resp.raise_for_status()
        for item in resp.json().get("items") or []:
            name = item.get("name") if isinstance(item, dict) else None
            if name:
                names.add(norm_title(base_title(name)))
    resp = session.get(STEAM_FEATURED, params={"cc": "UA", "l": "ukrainian"}, timeout=TIMEOUT)  # «Незабаром», новинки, хіти продажів
    resp.raise_for_status()
    body = resp.json()
    for block in ("coming_soon", "new_releases", "top_sellers"):
        for item in (body.get(block) or {}).get("items") or []:
            name = item.get("name") if isinstance(item, dict) else None
            if name:
                names.add(norm_title(base_title(name)))
    return names


def score(r: Release, popular: set[str]) -> int:
    """Скільки «довгоочікуваності»: 3 за присутність у популярному Steam, 2 за консольний ексклюзив-оригінал."""
    pts = 0
    if norm_title(base_title(r.title)) in popular:
        pts += 3
    if "ПК" not in r.platforms and r.kind.lower() in ("original", "remake", "reboot"):
        pts += 2
    return pts


def eligible(items: list[Release], popular: set[str]) -> list[Release]:
    return [r for r in items if r.notable and score(r, popular) >= 2]


def today_releases(items: list[Release], day: date, popular: set[str]) -> list[Release]:
    """Помітні релізи дня: найпопулярніші першими, далі ті, що на більшій кількості платформ."""
    found = [r for r in eligible(items, popular) if r.day == day]
    return sorted(found, key=lambda r: (-score(r, popular), -len(r.platforms), r.title))


def week_releases(items: list[Release], start: date, end: date, popular: set[str], limit: int = 8) -> list[Release]:
    found = [r for r in eligible(items, popular) if start <= r.day <= end]
    return sorted(found, key=lambda r: (r.day, -score(r, popular), r.title))[:limit]


def parse_day(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()
