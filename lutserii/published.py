"""Журнал опублікованих у канал постів (для щоденних Shorts: показуємо те, що реально вийшло)."""

from __future__ import annotations

import json
from typing import Any

from .db import StateDB

PREFIX = "pub:"
KEEP_SECONDS = 4 * 24 * 3600
# яка тема ролика яким типам постів відповідає
TOPIC_KINDS = {
    "games": ("deal", "free", "console-deal", "console-free", "console-sub", "release"),
    # для Shorts ігри розбито на два ролики: знижки з релізами та окремо безкоштовні роздачі
    "deals": ("deal", "console-deal", "release"),
    "free": ("free", "console-free", "console-sub"),
    "anime": ("anime-card", "anime"),
    "series": ("dub-card", "series", "dub"),
}


def record(db: StateDB, now_ts: int, kind: str, text: str, photos: list[str]) -> None:
    """Запам'ятовує пост (не довше KEEP_SECONDS), а старі записи прибирає."""
    db.set_cache(f"{PREFIX}{now_ts}:{abs(hash(text)) % 10**8}", json.dumps(
        {"ts": now_ts, "kind": kind, "text": text, "photos": photos[:3]}, ensure_ascii=False))
    for key in list(db.cache_items(PREFIX)):
        try:
            if int(key.split(":")[0]) < now_ts - KEEP_SECONDS:
                db.set_cache(f"{PREFIX}{key}", "")
        except ValueError:
            continue


def recent(db: StateDB, topic: str, since_ts: int) -> list[dict[str, Any]]:
    """Пости теми за період, найновіші першими; картки (з картинкою) раніше за довгі добірки."""
    kinds = TOPIC_KINDS[topic]
    out = []
    for raw in db.cache_items(PREFIX).values():
        if not raw:
            continue
        item = json.loads(raw)
        if item["kind"] not in kinds or item["ts"] < since_ts:
            continue
        if topic == "series" and item["kind"] == "dub-card" and "#аніме" in item["text"]:
            continue  # картка озвучки аніме не потрапляє в ролик про серіали
        out.append(item)
    out.sort(key=lambda i: (bool(i["photos"]), i["ts"]), reverse=True)
    return out
