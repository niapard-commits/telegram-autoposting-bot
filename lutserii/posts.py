"""Шаблони постів українською (HTML-розмітка Telegram)."""

from __future__ import annotations

from typing import Any

import html
import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from dataclasses import replace

from .dubs import Release, show_key
from .sources.anilist import AnimeInfo, Episode
from .sources.cheapshark import Deal
from .sources.gamerpower import Giveaway
from .telegram import CAPTION_LIMIT, visible_len

MONTHS = [
    "січня", "лютого", "березня", "квітня", "травня", "червня",
    "липня", "серпня", "вересня", "жовтня", "листопада", "грудня",
]

ANIME_GENRES = {
    "Action": "Екшн",
    "Adventure": "Пригоди",
    "Comedy": "Комедія",
    "Drama": "Драма",
    "Ecchi": "Еччі",
    "Fantasy": "Фентезі",
    "Horror": "Жахи",
    "Mahou Shoujo": "Махо-сьодзьо",
    "Mecha": "Меха",
    "Music": "Музика",
    "Mystery": "Детектив",
    "Psychological": "Психологія",
    "Romance": "Романтика",
    "Sci-Fi": "Наукова фантастика",
    "Slice of Life": "Повсякденність",
    "Sports": "Спорт",
    "Supernatural": "Надприродне",
    "Thriller": "Трилер",
    # жанри серіалів (TVMaze)
    "Science-Fiction": "Наукова фантастика",
    "Crime": "Кримінал",
    "Family": "Сімейний",
    "War": "Воєнний",
    "History": "Історичний",
    "Medical": "Медичний",
    "Legal": "Юридичний",
    "Western": "Вестерн",
    "Anime": "Аніме",
}


def esc(text: str) -> str:
    return html.escape(text, quote=True)


_SAFE_URL = re.compile(r"^https://[^\s\"'<>`\\]+$")


def safe_url(url: str) -> bool:
    """Лише https-посилання без пробілів і лапок: «javascript:», «tg://» тощо відкидаємо."""
    return bool(_SAFE_URL.match(url or ""))


def link(url: str, label: str) -> str:
    """Посилання з підписом; якщо адреса сумнівна, лише підпис без посилання."""
    return f'<a href="{esc(url)}">{label}</a>' if safe_url(url) else label


def usd(value: float) -> str:
    return f"${value:.2f}"


def hashtag(name: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-zА-Яа-яІіЇїЄєҐґ]", "", name)
    return f"#{cleaned}" if cleaned else ""


def long_date(day: date) -> str:
    return f"{day.day} {MONTHS[day.month - 1]}"


def ua_genres(genres: tuple[str, ...], limit: int = 3) -> list[str]:
    return [ANIME_GENRES[g] for g in genres if g in ANIME_GENRES][:limit]


def _quote(lines: list[str]) -> list[str]:
    """Блок-цитата Telegram; порожній блок не виводимо."""
    return ["<blockquote>" + "\n".join(lines) + "</blockquote>", ""] if lines else []


def _tail(tags: list[str], signature: str) -> list[str]:
    out = [" ".join(t for t in tags if t)]
    if signature:
        out.append(esc(signature))
    return out


def _uah_amount(line: str) -> str:
    """«🇺🇦 ≈ ₴159 за курсом НБУ» -> «₴159»; порожньо, якщо суми немає."""
    m = re.search(r"₴\s?(\d[\d\s.,]*)|(\d[\d\s.,]*)\s?₴", line)
    if not m:
        return ""
    return "₴" + (m.group(1) or m.group(2)).strip().rstrip(".,")


def _genre_tags(genres: tuple[str, ...]) -> list[str]:
    return [hashtag(g) for g in genres[:3]]


def deal_post(
    deal: Deal,
    store_name: str,
    blurb: str | None = None,
    genres: tuple[str, ...] = (),
    signature: str = "",
    uah: str = "",
    extras: tuple[str, ...] = (),
) -> str:
    """Заголовок-хук, опис, компактний блок із ціною, дедлайн і мінімум окремими рядками, кнопка-посилання."""
    price_now = _uah_amount(uah) or usd(deal.sale_price)
    where = f" у {esc(store_name)}" if store_name else ""
    lines = [
        f"🔥 <b>{esc(deal.title)} — знижка {round(deal.savings)}%, тепер {price_now}{where}</b>",
        "",
    ]
    if blurb:
        lines += [esc(blurb), ""]
    facts = [
        f"💰 <s>{usd(deal.normal_price)}</s> → <b>{usd(deal.sale_price)}</b> (−{round(deal.savings)}%)"
        + (f" · {esc(re.sub(r'^🇺🇦\s*', '', uah))}" if uah else ""),
        f"🏪 {esc(store_name or 'Магазин')}",
    ]
    if deal.steam_rating:
        facts.append(f"⭐ Відгуки в Steam: {deal.steam_rating}%")
    lines += _quote(facts)
    if extras:  # дата завершення, історичний мінімум
        lines += [esc(line) for line in extras] + [""]
    store_label = f"Забрати в {esc(store_name)}" if store_name else "До пропозиції"
    lines += [f"👉 {link(deal.url, store_label)}", ""]
    lines += _tail(["#знижка", hashtag(store_name), *_genre_tags(genres)], signature)
    return "\n".join(lines)


def deal_series_post(
    deals: list[Deal],
    store_name: str,
    franchise: str,
    blurb: str | None = None,
    genres: tuple[str, ...] = (),
    signature: str = "",
    uah: dict[str, str] | None = None,
    extras: tuple[str, ...] = (),
) -> str:
    """Кілька знижок однієї серії в одному пості: список ігор з цінами в цитаті (як у VGTimes)."""
    uah = uah or {}
    top = max(round(d.savings) for d in deals)
    where = f" у {esc(store_name)}" if store_name else ""
    lines = [
        f"🔥 <b>Серія {esc(franchise)}: знижки до {top}% на {len(deals)} {_games_word(len(deals))}{where}</b>",
        "",
    ]
    if blurb:
        lines += [esc(blurb), ""]
    lines += ["Зі знижкою віддають кілька частин, беремо:", ""]
    rows = []
    for d in deals:
        approx = f", {esc(uah[d.deal_id])}" if uah.get(d.deal_id) else ""
        rows.append(
            f"— {link(d.url, esc(d.title))} за <s>{usd(d.normal_price)}</s> "
            f"<b>{usd(d.sale_price)}</b> (−{round(d.savings)}%{approx})"
        )
    lines += _quote(rows)
    if extras:
        lines += [esc(line) for line in extras] + [""]
    lines += _tail(["#знижка", hashtag(store_name), *_genre_tags(genres)], signature)
    return "\n".join(lines)


def _games_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "гру"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "гри"
    return "ігор"


def format_end(end_date: str, tz: ZoneInfo) -> str | None:
    """'2026-10-30 17:00:00' (UTC) -> '30 жовтня, 19:00' у вашому часовому поясі."""
    try:
        dt = datetime.strptime(end_date, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    local = dt.astimezone(tz)
    return f"{long_date(local.date())}, {local:%H:%M}"


def giveaway_post(
    giveaway: Giveaway,
    tz: ZoneInfo,
    blurb: str | None = None,
    signature: str = "",
    genres: tuple[str, ...] = (),
) -> str:
    end = format_end(giveaway.end_date, tz)
    loot = getattr(giveaway, "kind", "game") == "loot"
    lines = [f"🎁 <b>Безкоштовно{' (додатковий вміст)' if loot else ''}: {esc(giveaway.title)}</b>", ""]
    if blurb:
        lines += [esc(blurb), ""]
    facts = []
    if giveaway.worth and giveaway.worth != "N/A":
        facts.append(f"💸 Зазвичай коштує <s>{esc(giveaway.worth)}</s>")
    if giveaway.platforms:
        facts.append(f"🖥 {esc(giveaway.platforms)}")
    lines += _quote(facts)
    if end:
        lines += [f"⏳ До {esc(end)}", ""]
    lines += [f"👉 {link(giveaway.url, 'Забрати безкоштовно')}", ""]
    lines += _tail(["#роздача", "#безкоштовно", *(["#лут"] if loot else []), *_genre_tags(genres)], signature)
    return "\n".join(lines)


def when_label(ts: int, tz: ZoneInfo, now_ts: int) -> str:
    local = datetime.fromtimestamp(ts, tz)
    delta = (local.date() - datetime.fromtimestamp(now_ts, tz).date()).days
    day = "Сьогодні" if delta == 0 else "Завтра" if delta == 1 else long_date(local.date())
    return f"{day} о {local:%H:%M}"


def anime_digest(episodes: list[Episode], day: date, tz: ZoneInfo, signature: str = "") -> str:
    lines = [f"📺 <b>Аніме сьогодні, {long_date(day)}</b>", ""]
    for ep in episodes:
        local = datetime.fromtimestamp(ep.airing_at, tz)
        title = esc(ep.display_title)
        if ep.url:
            title = link(ep.url, title)
        lines.append(f"🕒 {local:%H:%M} — {title}, серія {ep.episode}")
    lines.append("")
    lines += _tail(["#аніме", "#розклад"], signature)
    return "\n".join(lines)


def anime_card(
    ep: Episode, tz: ZoneInfo, now_ts: int, blurb: str | None = None, signature: str = ""
) -> str:
    lines = [f"🎬 <b>{esc(ep.display_title)}</b>", ""]
    if blurb:
        lines += [esc(blurb), ""]
    facts = [f"🕒 {when_label(ep.airing_at, tz, now_ts)}, серія {ep.episode}"]
    genres = ua_genres(ep.genres)
    if genres:
        facts.append(f"🏷 {esc(' · '.join(genres))}")
    lines += _quote(facts)
    links = " · ".join(link(url, esc(site)) for site, url in ep.streaming if safe_url(url))
    if links:
        lines += [f"📺 Де дивитись: {links}", ""]
    if ep.url:
        lines += [f"👉 {link(ep.url, 'Про аніме')}", ""]
    lines += _tail(["#аніме"], signature)
    return "\n".join(lines)


def series_card(
    show: Any, blurb: str | None, dub_lines: list[str], signature: str = ""
) -> str:
    """Пост про нову серію популярного серіалу: опис, що вийшло, жанри, чи є озвучка українською."""
    title = esc(show.name)
    if show.original_name and show.original_name.casefold() != show.name.casefold():
        title += f" <i>({esc(show.original_name)})</i>"
    lines = [f"📺 <b>{title}</b>", ""]
    if blurb:
        lines += [esc(blurb), ""]
    label = f"{show.season} сезон, {show.episode} серія"
    facts = [f"🆕 Вийшла: {esc(label)}" + (f" — «{esc(show.episode_name)}»" if show.episode_name else "")]
    if show.network:
        facts.append(f"📡 {esc(show.network)}")
    genres = ua_genres(show.genres)
    if genres:
        facts.append(f"🏷 {esc(' · '.join(genres))}")
    facts += dub_lines
    lines += _quote(facts)
    lines += [f"📖 {link(show.url, 'Про серіал')}", ""]
    lines += _tail(["#серіали", "#новасерія"], signature)
    return "\n".join(lines)


def dub_label(r: Release) -> str:
    """«Щоденник медика, 3 сезон, 5 серія» (діапазон: «5–6 серії»)."""
    parts = [r.title]
    if r.season:
        parts.append(f"{r.season} сезон")
    if r.episode and r.episode_to:
        parts.append(f"{r.episode}–{r.episode_to} серії")
    elif r.episode:
        parts.append(f"{r.episode} серія")
    return ", ".join(parts)


def _dub_row(r: Release, with_time: bool) -> str:
    """«🔹 <b>Назва</b> — 2 сезон, 4 серія»; для анонсів «🕒 18:00 · <b>Назва</b> — …»."""
    info = _episode_label(r)
    title = r.title if len(r.title) <= 56 else r.title[:56].rsplit(" ", 1)[0].rstrip(",:;—- ") + "…"
    row = f"<b>{esc(title)}</b>" + (f" — {esc(info)}" if info else "") + (" 🔒 для підписників" if r.premium else "")
    return (f"🕒 {r.time} · " if with_time and r.time else "🔹 ") + row


def _team_label(team: str, channel: str, links: bool) -> str:
    """Назва команди; якщо відомий її Telegram-канал, то клікабельна (окремого рядка про канали в кінці нема)."""
    name = f"<b>{esc(team)}</b>"
    return f'<a href="https://t.me/{esc(channel)}">{name}</a>' if links and channel else name


def _merge_rows(group: list[Release]) -> list[Release]:
    """Один серіал у команди це один рядок: «A / B» і «A» зливаємо, «2 серія» і «3–4 серії» стають «2–4 серії»."""
    merged: list[Release] = []
    for r in group:
        for i, m in enumerate(merged):
            if show_key(m.title) != show_key(r.title) or (m.season or 0) != (r.season or 0) or m.time != r.time:
                continue
            if not m.episode and not r.episode:
                pass  # обидва без номера серії
            elif m.episode and r.episode:
                lo, hi = min(m.episode, r.episode), max(m.episode_to or m.episode, r.episode_to or r.episode)
                span = (m.episode_to or m.episode) - m.episode + (r.episode_to or r.episode) - r.episode + 2
                if hi - lo + 1 > span:
                    continue  # між серіями розрив: лишаємо окремими рядками
            else:
                continue
            title = min(m.title, r.title, key=len)
            if m.episode and r.episode:
                lo = min(m.episode, r.episode)
                hi = max(m.episode_to or m.episode, r.episode_to or r.episode)
                merged[i] = replace(m, title=title, episode=lo, episode_to=hi if hi > lo else None,
                                    premium=m.premium and r.premium)
            else:
                merged[i] = replace(m, title=title, premium=m.premium and r.premium)
            break
        else:
            merged.append(r)
    return merged


def _dub_groups(items: list[Release], with_time: bool, links: bool = False) -> list[str]:
    """Команда один раз жирним, під нею цитата зі списком проєктів."""
    teams: dict[str, list[Release]] = {}
    for r in items:
        teams.setdefault(r.team, []).append(r)
    teams = {team: _merge_rows(group) for team, group in teams.items()}
    lines: list[str] = []
    for team, group in teams.items():
        channel = next((r.channel for r in group if r.channel), "")
        lines += [f"🎧 {_team_label(team, channel, links)}"] + _quote([_dub_row(r, with_time) for r in group])
    return lines


def dub_digest(
    released: list[Release],
    upcoming: list[Release],
    day: date,
    signature: str = "",
    links: bool = False,
    tags: tuple[str, ...] = ("#аніме",),
) -> str:
    """Добірка озвучки українською: команда один раз, назви жирним, емодзі-маркери."""
    lines = [f"🎙 <b>Озвучка українською · {long_date(day)}</b>", ""]
    if released:
        lines += ["✅ <b>Щойно вийшло</b>", ""] + _dub_groups(released, False, links)
    if upcoming:
        lines += ["⏰ <b>Сьогодні очікується</b>", ""] + _dub_groups(upcoming, True, links)
    lines += _tail(["#озвучка", *tags, "#українською"], signature)
    return "\n".join(lines)


def _episode_label(r: Release) -> str:
    parts = []
    if r.season:
        parts.append(f"{r.season} сезон")
    if r.episode and r.episode_to:
        parts.append(f"{r.episode}–{r.episode_to} серії")
    elif r.episode:
        parts.append(f"{r.episode} серія")
    return ", ".join(parts)


def dub_digest_fit(
    released: list[Release],
    upcoming: list[Release],
    day: date,
    signature: str = "",
    links: bool = False,
    limit: int = CAPTION_LIMIT - 24,
    tags: tuple[str, ...] = ("#аніме",),
) -> tuple[str, list[Release], list[Release]]:
    """Добірка, що вміщається в підпис до альбому: зайве з кінця відкидаємо (воно піде в наступну добірку)."""
    rel, up = list(released), list(upcoming)
    while True:
        text = dub_digest(rel, up, day, signature, links, tags)
        if visible_len(text) <= limit or not (rel or up):
            return text, rel, up
        # прибираємо останній рядок команди, у якої їх найбільше: так кожна команда (і серіали) лишається в добірці
        counts: dict[str, int] = {}
        for r in rel + up:
            counts[r.team] = counts.get(r.team, 0) + 1
        busiest = max(counts, key=lambda t: counts[t])
        pool = up if any(r.team == busiest for r in up) else rel
        index = max(i for i, r in enumerate(pool) if r.team == busiest)
        pool.pop(index)


def dub_card(
    r: Release, info: AnimeInfo, blurb: str, signature: str = "", links: bool = False,
    show_site: bool = True,
) -> str:
    """Окремий пост про реліз озвучки: назва, опис, що вийшло, жанри, де дивитись."""
    lines = [f"🎙 <b>{esc(r.title)}</b>", "", esc(blurb), ""]
    label = _episode_label(r)
    if r.status == "announced":
        when = f"Сьогодні о {r.time}" if r.time else "Сьогодні"
        facts = [f"🕒 {when}: {esc(label)}" if label else f"🕒 {when}"]
    else:
        facts = [f"✅ Вийшло: {esc(label)}" if label else "✅ Вийшла озвучка"]
    if r.premium:
        facts.append("🔒 Лише для підписників команди")
    if r.site and r.site == r.team:  # знайдено на сайті, а не в дописі команди: команду озвучки не знаємо
        facts[0] = "✅ Нова серія українською"
        facts.append(f"🌐 Дивитись: {esc(r.site)}")
    else:
        team = f'<a href="https://t.me/{esc(r.channel)}">{esc(r.team)}</a>' if links and r.channel else esc(r.team)
        facts.append(f"🎧 Озвучка: {team}")
        if show_site and r.site:
            facts.append(f"🌐 Сайт: {esc(r.site)}")
    genres = ua_genres(info.genres)
    if genres:
        facts.append(f"🏷 {esc(' · '.join(genres))}")
    lines += _quote(facts)
    if info.url:
        lines += [f"📖 {link(info.url, 'Про серіал' if info.kind == 'series' else 'Про аніме')}", ""]
    lines += _tail(["#озвучка", "#серіали" if info.kind == "series" else "#аніме"], signature)
    return "\n".join(lines)


# ---------- консолі ----------

MONTHS_EN_UA = {
    "january": "січні", "february": "лютому", "march": "березні", "april": "квітні", "may": "травні",
    "june": "червні", "july": "липні", "august": "серпні", "september": "вересні",
    "october": "жовтні", "november": "листопаді", "december": "грудні",
}


def uah(value: float) -> str:
    """2599.0 -> «₴2 599»; 215.88 -> «₴215,88»."""
    if abs(value - round(value)) < 0.005:
        return "₴" + f"{round(value):,}".replace(",", " ")
    return "₴" + f"{value:,.2f}".replace(",", " ").replace(".", ",")


def _console_facts(deal) -> list[str]:
    return [
        f"💰 <s>{uah(deal.price_old)}</s> → <b>{uah(deal.price_new)}</b> (−{deal.discount}%)",
        f"🎮 {esc(' · '.join(deal.platforms))}",
        f"🏪 {esc(deal.store)} (Україна)",
    ]


def console_deal_post(deal, signature: str = "") -> str:
    lines = [
        f"🔥 <b>{esc(deal.title)} — знижка {deal.discount}%, тепер {uah(deal.price_new)} у {esc(deal.store)}</b>",
        "",
        *_quote(_console_facts(deal)),
        f"👉 {link(deal.url, 'Деталі та ціна')}",
        "",
        "<i>Дані: PSprices</i>",
        *_tail(["#знижка", f"#{deal.family}"], signature),
    ]
    return "\n".join(lines)


def console_free_post(deal, signature: str = "") -> str:
    lines = [
        f"🎁 <b>{esc(deal.title)} — безкоштовно у {esc(deal.store)}</b>",
        "",
        *_quote([
            f"💰 <s>{uah(deal.price_old)}</s> → <b>безкоштовно</b>",
            f"🎮 {esc(' · '.join(deal.platforms))}",
            f"🏪 {esc(deal.store)} (Україна)",
        ]),
        f"👉 {link(deal.url, 'Забрати')}",
        "",
        "<i>Дані: PSprices</i>",
        *_tail(["#роздача", f"#{deal.family}"], signature),
    ]
    return "\n".join(lines)


def subscription_post(item, signature: str = "") -> str:
    if item.kind == "psplus":
        month = MONTHS_EN_UA.get(item.label.casefold(), "цьому місяці")
        head = f"🎮 <b>PlayStation Plus у {month}: ігри місяця вже відомі</b>"
        intro, tags, source = "Передплатникам PS Plus Essential додадуть:", ["#PSPlus", "#PlayStation"], "Дивитись оголошення"
    elif item.kind == "gamepass":
        head = "🟢 <b>Нове в Xbox Game Pass</b>"
        intro, tags, source = "Незабаром у каталозі:", ["#GamePass", "#Xbox"], "Дивитись оголошення"
    else:
        head = "🆓 <b>Безкоштовні вихідні в Xbox: Free Play Days</b>"
        intro, tags, source = "Пограти можна безкоштовно, без підписки:", ["#роздача", "#Xbox"], "Дивитись оголошення"
    lines = [head, "", intro, "", *_quote([f"— {esc(g)}" for g in item.games[:8]]),
             f"👉 {link(item.url, source)}", "", *_tail(tags, signature)]
    return "\n".join(lines)


def release_post(title: str, day: date, platforms: tuple[str, ...], blurb: str | None = None,
                 genres: tuple[str, ...] = (), price: str = "", url: str = "", signature: str = "") -> str:
    lines = [f"🚀 <b>Сьогодні вийшла: {esc(title)}</b>", ""]
    if blurb:
        lines += [esc(blurb), ""]
    facts = [f"🎮 {esc(' · '.join(platforms))}"]
    if price:
        facts.append(f"💰 У Steam: {esc(price)}")
    lines += _quote(facts)
    if url:
        lines += [f"👉 {link(url, 'Сторінка в Steam')}", ""]
    tags = ["#реліз", "#новинки", *_genre_tags(genres), *[hashtag(p) for p in platforms if p != "ПК"][:2]]
    lines += _tail(tags, signature)
    return "\n".join(lines)


def release_week_post(items: list[tuple[date, str, tuple[str, ...]]], start: date, end: date, signature: str = "") -> str:
    span = f"{start.day}–{end.day} {MONTHS[end.month - 1]}" if start.month == end.month else f"{long_date(start)} – {long_date(end)}"
    lines = [f"🗓 <b>Релізи тижня: {span}</b>", ""]
    for day, title, platforms in items:
        lines.append(f"• {long_date(day)}: <b>{esc(title)}</b> ({esc(', '.join(platforms))})")
    lines += ["", "Нагадаємо в день виходу 🔔", ""]
    lines += _tail(["#реліз", "#новинки", "#ігри"], signature)
    return "\n".join(lines)
