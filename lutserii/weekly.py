"""П'ятнична добірка «Підсумки тижня»: найкращі знижки, роздачі на вихідні, що вийшло українською.
Факти запам'ятовуємо в момент публікації постів (кеш бази), а в п'ятницю збираємо їх в один пост."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from . import posts
from .db import StateDB

PREFIX = "week:"
KEEP_DAYS = 21  # давніше за це з кешу прибираємо
DEALS_SHOWN = 5
FREE_SHOWN = 6
DUB_TEAMS_SHOWN = 5
DUB_TITLES_PER_TEAM = 6
POST_HOUR = 18  # не раніше цієї години за київським часом


def _record(db: StateDB, kind: str, ts: int, ident: str, data: dict) -> None:
    db.set_cache(f"{PREFIX}{kind}:{ts}:{ident}", json.dumps(data, ensure_ascii=False))


def record_deal(db: StateDB, ts: int, deal: Any, store: str, uah: str = "") -> None:
    _record(db, "deal", ts, str(deal.deal_id), {
        "title": deal.title, "store": store, "price": deal.sale_price, "normal": deal.normal_price,
        "savings": deal.savings, "rating": deal.rating, "uah": uah, "url": deal.url,
    })


def record_free(db: StateDB, ts: int, giveaway: Any) -> None:
    _record(db, "free", ts, str(giveaway.id), {
        "title": giveaway.title, "worth": giveaway.worth, "end": giveaway.end_date,
        "url": giveaway.url, "kind": getattr(giveaway, "kind", "game"),
    })


def record_dub(db: StateDB, ts: int, release: Any) -> None:
    _record(db, "dub", ts, release.ident, {
        "title": release.title, "team": release.team, "channel": release.channel, "site": release.site or "",
    })


def load(db: StateDB, now_ts: int) -> dict[str, list[tuple[int, dict]]]:
    """Збережені факти за останні KEEP_DAYS діб; старіші видаляємо."""
    out: dict[str, list[tuple[int, dict]]] = {"deal": [], "free": [], "dub": []}
    limit = now_ts - KEEP_DAYS * 86400
    for key, raw in db.cache_items(PREFIX).items():
        try:
            kind, ts, _ident = key.split(":", 2)
            ts_int, data = int(ts), json.loads(raw)
        except (ValueError, TypeError):
            continue
        if ts_int < limit:
            db.conn.execute("DELETE FROM cache WHERE key = ?", (f"{PREFIX}{key}",))
            continue
        if kind in out and isinstance(data, dict):
            out[kind].append((ts_int, data))
    db.conn.commit()
    return out


def _range(start: date, end: date) -> str:
    if start.month == end.month:
        return f"{start.day}–{end.day} {posts.MONTHS[end.month - 1]}"
    return f"{posts.long_date(start)} – {posts.long_date(end)}"


def _end_ts(end: str) -> int | None:
    try:
        return int(datetime.strptime(end, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return None


def build_text(facts: dict[str, list[tuple[int, dict]]], now_ts: int, tz: ZoneInfo, signature: str = "") -> str | None:
    week_ago = now_ts - 7 * 86400
    today = datetime.fromtimestamp(now_ts, tz).date()
    lines = [f"🗓 <b>Підсумки тижня · {_range(today - timedelta(days=6), today)}</b>", ""]
    body = 0

    deals: dict[str, dict] = {}
    for ts, d in facts["deal"]:
        if ts < week_ago:
            continue
        key = str(d.get("title", "")).casefold()
        if key and (key not in deals or (d.get("rating", 0), d.get("savings", 0)) > (deals[key].get("rating", 0), deals[key].get("savings", 0))):
            deals[key] = d
    top = sorted(deals.values(), key=lambda d: (d.get("rating", 0), d.get("savings", 0)), reverse=True)[:DEALS_SHOWN]
    if top:
        rows = []
        for d in top:
            price = posts.usd(float(d.get("price", 0)))
            uah = f" ({d['uah']})" if d.get("uah") else ""
            rows.append(
                f"🔹 {posts.link(d.get('url', ''), '<b>' + posts.esc(d['title']) + '</b>')} — "
                f"−{round(float(d.get('savings', 0)))}% · {price}{uah} · {posts.esc(d.get('store', ''))}"
            )
        lines += ["🔥 <b>Найкращі знижки</b>", ""] + posts._quote(rows)
        body += 1

    free = []
    for ts, g in sorted(facts["free"], key=lambda t: t[0], reverse=True):
        end = _end_ts(str(g.get("end", "")))
        if end is not None and end < now_ts:
            continue  # роздача вже закінчилась
        if end is None and ts < week_ago:
            continue  # дата невідома, а роздача давня: не ризикуємо
        free.append((end, g))
    free = free[:FREE_SHOWN]
    if free:
        rows = []
        for end, g in free:
            until = f" — до {posts.long_date(datetime.fromtimestamp(end, tz).date())}" if end else ""
            rows.append(f"🎁 {posts.link(g.get('url', ''), '<b>' + posts.esc(g['title']) + '</b>')}{until}")
        lines += ["🎮 <b>Безкоштовно на вихідні</b>", ""] + posts._quote(rows)
        body += 1

    teams: dict[str, dict] = {}
    for ts, r in facts["dub"]:
        if ts < week_ago or not r.get("title"):
            continue
        entry = teams.setdefault(r.get("team") or "", {"channel": r.get("channel", ""), "titles": []})
        if r["title"] not in entry["titles"]:
            entry["titles"].append(r["title"])
    ranked = sorted(((t, e) for t, e in teams.items() if t), key=lambda kv: -len(kv[1]["titles"]))[:DUB_TEAMS_SHOWN]
    if ranked:
        lines += ["🎙 <b>Українською цього тижня</b>", ""]
        for team, entry in ranked:
            shown = entry["titles"][:DUB_TITLES_PER_TEAM]
            more = len(entry["titles"]) - len(shown)
            names = ", ".join(posts.esc(t) for t in shown) + (f" та ще {more}" if more > 0 else "")
            lines += [f"🎧 {posts._team_label(team, entry['channel'], True)}"] + posts._quote([names])
        body += 1

    if not body:
        return None
    lines += ["Гарних вихідних і приємної гри з друзями! 🎮", "", "Ціни можуть змінитися: перед покупкою перевіряйте магазин.", ""]
    lines += posts._tail(["#підсумкитижня", "#знижки", "#роздача"], signature)
    return "\n".join(lines)


def is_time(now_ts: int, tz: ZoneInfo) -> bool:
    local = datetime.fromtimestamp(now_ts, tz)
    return local.weekday() == 4 and local.hour >= POST_HOUR  # п'ятниця, вечір


def week_key(now_ts: int, tz: ZoneInfo) -> str:
    year, week, _ = datetime.fromtimestamp(now_ts, tz).isocalendar()
    return f"weekly:{year}-{week}"
