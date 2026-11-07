"""Безкоштовні роздачі ігор: GamerPower (без ключа)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..textutil import clean_text

API ="https://www.gamerpower.com/api/giveaways"
TIMEOUT = 20


@dataclass(frozen=True)
class Giveaway:
    id: int
    title: str
    worth: str  # наприклад "$19.99" або "N/A"
    platforms: str
    end_date: str  # "YYYY-MM-DD HH:MM:SS" (UTC) або "N/A"
    published: str
    url: str
    image: str | None
    description: str = ""  # англійською, як у GamerPower
    kind: str = "game"  # game — гра; loot — внутрішньоігровий вміст і DLC


def parse_giveaway(raw: dict, kind: str = "game") -> Giveaway:
    title = re.sub(r"\s+Giveaway$", "", str(raw.get("title", "")).strip())
    url = raw.get("open_giveaway_url") or raw.get("open_giveaway") or raw.get("gamerpower_url") or ""
    return Giveaway(
        id=int(raw["id"]),
        title=title,
        worth=str(raw.get("worth", "N/A")),
        platforms=str(raw.get("platforms", "")),
        end_date=str(raw.get("end_date", "N/A")),
        published=str(raw.get("published_date", "")),
        url=str(url),
        image=raw.get("image") or raw.get("thumbnail") or None,
        description=clean_text(raw.get("description")),
        kind=kind,
    )


def fetch_giveaways(session: Any, kind: str = "game") -> list[Giveaway]:
    """Активні роздачі на ПК (kind: game або loot), найновіші першими."""
    resp = session.get(
        API,
        params={"type": kind, "platform": "pc", "sort-by": "date"},
        timeout=TIMEOUT,
    )
    if resp.status_code == 201:  # активних роздач немає
        return []
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        return []
    items = [
        parse_giveaway(item, kind)
        for item in data
        if item.get("status", "Active") == "Active" and item.get("id") is not None
    ]
    items = [g for g in items if g.url]
    return sorted(items, key=lambda g: g.published, reverse=True)
