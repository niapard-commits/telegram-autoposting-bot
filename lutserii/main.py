"""Запуск: збір даних, фільтр якості, публікація в канал."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests

from . import dubs, posts, probe, published, telegram, weekly
from .config import Config
from .db import StateDB
from .ai import PROMPT_VERSION, Editor
from .channel import run_avatar, run_setup
from .sources import anilist, cheapshark, consoles, gamerpower, itad, jikan, nbu, releases, steam, tgchannel, tmdb, tvmaze
from .textutil import (
    base_title, fix_terms, franchise_key, franchise_name, has_cyrillic, norm_title, shorten, titles_match,
    whole_sentences,
)
from .titles import load_titles, lookup_title

USER_AGENT = "lut-i-serii-bot/0.1 (Telegram channel bot)"
PAUSE_BETWEEN_POSTS = 2.0
MAX_DUB_COVERS = 4  # 3-4 обкладинки в альбомі виглядають найкраще
CHANNELS_OUT = "data/probe/channels.json"
MAX_SERIES = 6  # скільки ігор однієї серії влазить в один пост
MAX_DEFERS = 3  # скільки запусків чекаємо на ШІ, перш ніж публікувати пост без опису


@dataclass
class Context:
    cfg: Config
    session: Any
    db: StateDB
    send: Callable[[str, list[str]], None]
    clock: Callable[[], float] = time.time
    pause: float = PAUSE_BETWEEN_POSTS
    log: Callable[[str], None] = field(default=print)
    editor: Any = None  # ШІ-редактор або None
    extractor: Any = None  # простий розбір анонсів озвучки (Gemini, якщо є); без нього працює editor
    compare_editors: list = field(default_factory=list)  # [(підпис, редактор)] для task=compare
    alt_editor: Any = None  # другий ШІ-редактор (лише для порівняння task=compare)
    preview: Callable[[str, list[str]], None] | None = None  # надсилає перегляд вам у приват
    send_video: Callable[[str, str, list[str]], None] | None = None  # (текст, відео, запасні картинки)
    preview_video: Callable[[str, str, list[str]], None] | None = None

    def send_preview(self, text: str, photos: list[str], video: str | None = None) -> None:
        """Перегляд у приватний чат; збій тут не повинен ламати запуск."""
        if self.preview is None:
            return
        try:
            if video and self.preview_video is not None:
                self.preview_video(text, video, photos)
            else:
                self.preview(text, photos)
        except Exception as exc:
            self.log(f"Перегляд у Telegram не надіслано ({type(exc).__name__}: {exc})")
        time.sleep(self.pause)

    def publish(
        self,
        kind: str,
        keys: list[str],
        text: str,
        photos: list[str] | str | None = None,
        video: str | None = None,
    ) -> None:
        """Шле пост і запам'ятовує ключі. У dry-run лише друкує. Відео (трейлер) іде, якщо його вміє слати канал."""
        if isinstance(photos, str):
            photos = [photos]
        photos = [p for p in (photos or []) if p]
        video = video if (video and self.cfg.game_video) else None
        if self.cfg.dry_run:
            self.log("----- DRY RUN: так виглядав би пост -----")
            self.log(text)
            self.log(f"(картинок: {len(photos)}: {', '.join(photos)})" if photos else "(без картинки)")
            if video:
                self.log(f"(відео: {video})")
            self.send_preview(text, photos, video)
            return
        if video and self.send_video is not None:
            self.send_video(text, video, photos)
        else:
            self.send(text, photos)
        self.db.mark_posted(keys[0], kind)
        published.record(self.db, int(self.clock()), kind, text, photos)
        for extra in keys[1:]:
            self.db.mark_posted(extra, f"{kind}-alias")
        time.sleep(self.pause)


class DeferPost(Exception):
    """Пост відкладено до наступного запуску: ШІ тимчасово недоступний (ліміт чи перевантаження)."""


def ai_blurb(
    ctx: Context, kind: str, title: str, text: str, cache_key: str, defer_key: str,
    can_skip: bool = False, alt_text: Callable[[], str] | None = None,
) -> str | None:
    """Опис від ШІ. Якщо ШІ тимчасово недоступний, пост відкладаємо (не більше MAX_DEFERS разів).
    Якщо в основному тексті немає сюжету, пробуємо запасне джерело (alt_text)."""
    if not ctx.editor:
        return None
    blurb, failed = None, False
    if text:
        blurb = ctx.editor.blurb(kind, title, text, cache_key)
        failed = getattr(ctx.editor, "failed", False)
    if blurb is None and not failed and alt_text is not None:
        try:
            extra = alt_text()
        except Exception as exc:
            ctx.log(f"Запасне джерело опису недоступне ({type(exc).__name__}).")
            extra = ""
        if extra and extra != text:
            blurb = ctx.editor.blurb(kind, title, extra, f"{cache_key}:alt")
            failed = getattr(ctx.editor, "failed", False)
    if blurb is None and failed and not ctx.cfg.dry_run and not can_skip:
        tries = int(ctx.db.get_cache(f"defer:{defer_key}") or 0) + 1
        ctx.db.set_cache(f"defer:{defer_key}", str(tries))
        if tries <= MAX_DEFERS:
            ctx.log(f"ШІ зараз недоступний, відкладаю «{title}» (спроба {tries} з {MAX_DEFERS}).")
            raise DeferPost(defer_key)
    return fix_terms(blurb) if blurb else blurb


def mal_synopsis(ctx: Context, mal_id: int) -> str:
    """Запасний опис аніме з MyAnimeList за номером; результат (і «немає») кешуємо."""
    if not mal_id:
        return ""
    key = f"mal:{mal_id}"
    cached = ctx.db.get_cache(key)
    if cached is not None:
        return "" if cached == "-" else cached
    text = jikan.fetch_synopsis(ctx.session, mal_id)
    ctx.db.set_cache(key, text or "-")
    return text


def steam_description(ctx: Context, title: str) -> str:
    """Запасний опис гри зі Steam за назвою (для роздач, де свого опису немає чи він порожній)."""
    name = re.sub(r"\s*\([^)]*\)\s*$", "", title).strip()
    app_id = find_steam_app(ctx, name) if name else None
    if not app_id:
        return ""
    info = steam.fetch_app(ctx.session, app_id)
    return info.description if info else ""


def start_of_local_day(tz: ZoneInfo, now: datetime | None = None) -> datetime:
    local_now = (now or datetime.now(timezone.utc)).astimezone(tz)
    return local_now.replace(hour=0, minute=0, second=0, microsecond=0)


def pick_deals(
    deals: list[cheapshark.Deal],
    cfg: Config,
    db: StateDB,
    now: datetime | None = None,
) -> list[cheapshark.Deal]:
    """Лишає лише варті уваги знижки, яких ми ще не постили, найкращі першими."""
    best: dict[str, cheapshark.Deal] = {}
    for deal in deals:
        if deal.sale_price <= 0:  # безкоштовне віддаємо розділу «роздачі»
            continue
        if deal.savings < cfg.min_discount or deal.rating < cfg.min_deal_rating:
            continue
        title_key = norm_title(base_title(deal.title))  # видання однієї гри (Deluxe, Ultimate) = одна гра
        if db.was_posted(f"deal:{deal.deal_id}", cfg.deal_repeat_days, now):
            continue
        if db.was_posted(f"title:{title_key}", cfg.deal_repeat_days, now) or db.was_posted(
            f"title:{deal.title.casefold()}", cfg.deal_repeat_days, now
        ):
            continue
        current = best.get(title_key)
        if current is None or (deal.rating, deal.savings) > (current.rating, current.savings):
            best[title_key] = deal
    return sorted(best.values(), key=lambda d: (d.rating, d.steam_rating or 0, d.savings), reverse=True)


def trusted_store_ids(stores: dict[str, str], cfg: Config) -> list[str]:
    """Номери офіційних магазинів: просимо в CheapShark лише їх, щоб сірі перепродавці не займали сторінки."""
    trusted = {name.casefold() for name in cfg.trusted_stores}
    return sorted(sid for sid, name in stores.items() if name.casefold() in trusted)


def filter_trusted(
    deals: list[cheapshark.Deal],
    stores: dict[str, str],
    cfg: Config,
    log: Callable[[str], None] = print,
) -> list[cheapshark.Deal]:
    """Лишає знижки лише з офіційних магазинів (сірі перепродавці ключів відпадають)."""
    if not cfg.trusted_stores:
        return deals
    trusted = {name.casefold() for name in cfg.trusted_stores}
    kept = [d for d in deals if stores.get(d.store_id, "").casefold() in trusted]
    dropped = {stores.get(d.store_id, "?") for d in deals} - {
        stores.get(d.store_id, "?") for d in kept
    }
    if dropped:
        log(f"Знижки: пропущено магазини поза списком: {', '.join(sorted(dropped))}")
    return kept


def group_deals(deals: list[cheapshark.Deal]) -> list[list[cheapshark.Deal]]:
    """Знижки однієї серії в одному магазині складаємо в групу; порядок за найкращою знижкою групи."""
    groups: dict[tuple[str, str], list[cheapshark.Deal]] = {}
    for deal in deals:  # deals уже відсортовані: перша в групі найкраща
        key = franchise_key(deal.title)
        groups.setdefault((deal.store_id, key or deal.deal_id), []).append(deal)
    return [group[:MAX_SERIES] for group in groups.values()]


def uah_approx(ctx: Context, price: float) -> str:
    rate = usd_rate(ctx)
    return f"≈ ₴{round(price * rate)}" if rate and price > 0 else ""


def deal_post_text(
    ctx: Context, group: list[cheapshark.Deal], store: str,
) -> tuple[str, list[str], str | None] | None:
    """Текст, картинки й трейлер для однієї знижки чи серії; None, якщо пост відкладено через ШІ."""
    cfg = ctx.cfg
    leader = group[0]
    try:
        blurb, genres, photos, steam_price, trailer = enrich_deal(ctx, leader)
    except DeferPost:
        return None
    extras = itad_extras(ctx, leader, store)
    if len(group) == 1:
        text = posts.deal_post(
            leader, store, blurb, genres, cfg.signature, uah_line(ctx, leader, store, steam_price),
            extras,
        )
        return text, photos, trailer
    uah = {d.deal_id: uah_approx(ctx, d.sale_price) for d in group}
    text = posts.deal_series_post(
        sorted(group, key=lambda d: d.title.casefold()), store, franchise_name(leader.title),
        blurb, genres, cfg.signature, uah, tuple(e for e in extras if e.startswith("⏳")),
    )
    return text, photos, trailer


def deal_keys(group: list[cheapshark.Deal]) -> list[str]:
    keys = [f"deal:{group[0].deal_id}"]
    for deal in group:
        keys += [
            f"deal:{deal.deal_id}",
            f"title:{deal.title.casefold()}",
            f"title:{norm_title(base_title(deal.title))}",
        ]
    return list(dict.fromkeys(keys))


def run_deals(ctx: Context) -> int:
    cfg = ctx.cfg
    tz = ZoneInfo(cfg.timezone)
    posted_today = ctx.db.count_since("deal", start_of_local_day(tz))
    budget = min(cfg.max_deals_per_run, cfg.max_deals_per_day - posted_today)
    if budget <= 0:
        ctx.log(f"Знижки: денний ліміт ({cfg.max_deals_per_day}) вичерпано.")
        return 0
    stores = cheapshark.fetch_stores(ctx.session)
    trusted_ids = trusted_store_ids(stores, cfg)
    deals = cheapshark.fetch_deals_wide(ctx.session, trusted_ids or None)
    deals = filter_trusted(deals, stores, cfg, ctx.log)
    groups = group_deals(pick_deals(deals, cfg, ctx.db))[:budget]
    posted = 0
    for group in groups:
        store = stores.get(group[0].store_id, "")
        built = deal_post_text(ctx, group, store)
        if built is None:
            continue
        text, photos, trailer = built
        ctx.publish("deal", deal_keys(group), text, photos, trailer)
        if not cfg.dry_run:
            weekly.record_deal(ctx.db, int(ctx.clock()), group[0], store, uah_approx(ctx, group[0].sale_price))
        posted += 1
    ctx.log(f"Знижки: опубліковано {posted}.")
    return posted


def find_steam_app(ctx: Context, title: str) -> str | None:
    """Шукає базову гру в Steam за назвою; результат (і «не знайдено») кешуємо."""
    key = f"steam-id:{norm_title(base_title(title))}"
    cached = ctx.db.get_cache(key)
    if cached is not None:
        return None if cached == "-" else cached
    try:
        app_id = steam.search_app_id(ctx.session, title)
    except Exception as exc:
        ctx.log(f"Steam: пошук за назвою не вдався ({type(exc).__name__}).")
        return None
    ctx.db.set_cache(key, app_id or "-")
    return app_id


def usd_rate(ctx: Context) -> float | None:
    """Курс НБУ; один запит на добу, далі з кешу."""
    key = f"nbu-usd:{datetime.fromtimestamp(int(ctx.clock()), timezone.utc).date()}"
    cached = ctx.db.get_cache(key)
    if cached is not None:
        return float(cached) if cached != "-" else None
    try:
        rate = nbu.fetch_usd_rate(ctx.session)
    except Exception as exc:
        ctx.log(f"НБУ: курс не отримано ({type(exc).__name__}).")
        return None
    ctx.db.set_cache(key, str(rate) if rate else "-")
    return rate


def uah_line(ctx: Context, deal: cheapshark.Deal, store: str, steam_price: tuple[str, str]) -> str:
    """Ціна в гривнях: точна зі Steam України або приблизна за курсом НБУ."""
    final, initial = steam_price
    if store.casefold() == "steam" and final:
        return f"🇺🇦 У Steam України: {final}" + (f" (було {initial})" if initial else "")
    rate = usd_rate(ctx)
    if rate and deal.sale_price > 0:
        return f"🇺🇦 ≈ ₴{round(deal.sale_price * rate)} за курсом НБУ"
    return ""


def itad_extras(ctx: Context, deal: cheapshark.Deal, store: str) -> tuple[str, ...]:
    """Дата завершення знижки й історичний мінімум з IsThereAnyDeal; без ключа чи при збої порожньо."""
    key = ctx.cfg.itad_api_key
    if not key:
        return ()
    try:
        cache_key = f"itad-id:{deal.steam_app_id or norm_title(deal.title)}"
        game_id = ctx.db.get_cache(cache_key)
        if game_id is None:
            game_id = itad.lookup_game(ctx.session, key, deal.title, deal.steam_app_id) or "-"
            ctx.db.set_cache(cache_key, game_id)
        if game_id == "-":
            return ()
        info = itad.parse_info(itad.fetch_prices(ctx.session, key, game_id), store)
    except Exception as exc:
        ctx.log(f"ITAD: дані не отримано ({type(exc).__name__}).")
        return ()
    if info is None:
        return ()
    tz = ZoneInfo(ctx.cfg.timezone)
    lines: list[str] = []
    now = datetime.fromtimestamp(int(ctx.clock()), timezone.utc)
    if info.expiry and info.expiry > now:
        local = info.expiry.astimezone(tz)
        lines.append(f"⏳ До {posts.long_date(local.date())}, {local:%H:%M}")
    if info.history_low is not None:
        price = info.price if info.price is not None else deal.sale_price
        if price <= info.history_low * 1.03:
            lines.append("🏆 Історичний мінімум ціни")
        else:
            lines.append(f"📉 Мінімум за весь час: {posts.usd(info.history_low)}")
    return tuple(lines)


def enrich_deal(
    ctx: Context, deal: cheapshark.Deal
) -> tuple[str | None, tuple[str, ...], list[str], tuple[str, str], str | None]:
    """Опис, жанри, картинки, ціна в гривнях і трейлер зі Steam. Збій тут не має ламати публікацію."""
    photos = [deal.image] if deal.image else []
    app_id = deal.steam_app_id
    if not app_id:
        app_id = find_steam_app(ctx, deal.title)
        if app_id:
            photos = [steam.header_image(app_id)]
    if not app_id:
        return None, (), photos, ("", ""), None
    try:
        info = steam.fetch_app(ctx.session, app_id)
    except Exception as exc:
        ctx.log(f"Steam: не вдалося отримати опис ({type(exc).__name__}).")
        return None, (), photos, ("", ""), None
    if info is None:
        return None, (), photos, ("", ""), None
    ukrainian = whole_sentences(info.description, 480) if has_cyrillic(info.description) else None
    blurb = ai_blurb(
        ctx, "game", deal.title, info.description, f"ai:game:{app_id}",
        f"deal:{deal.deal_id}", can_skip=ukrainian is not None,
    )
    if blurb is None:
        blurb = ukrainian  # Steam віддав український переклад
    genres = tuple(g for g in info.genres if has_cyrillic(g))[:3]
    photos += list(info.screenshots[:3])
    return blurb, genres, photos, (info.price_final, info.price_initial), info.trailer or None


def enrich_giveaway(
    ctx: Context, giveaway: gamerpower.Giveaway
) -> tuple[list[str], tuple[str, ...]]:
    """Як у знижок: якщо гра є в Steam, то обкладинка + 2 скріншоти й жанри; інакше картинка роздачі."""
    photos = [giveaway.image] if giveaway.image else []
    name = re.sub(r"\s*\([^)]*\)\s*$", "", giveaway.title).strip()  # «Назва (Epic Games)» -> «Назва»
    app_id = find_steam_app(ctx, name) if name else None
    if not app_id:
        return photos, ()
    try:
        info = steam.fetch_app(ctx.session, app_id)
    except Exception as exc:
        ctx.log(f"Steam: не вдалося отримати скріншоти ({type(exc).__name__}).")
        return photos, ()
    if info is None or not info.screenshots:
        return photos, ()
    genres = tuple(g for g in info.genres if has_cyrillic(g))[:3]
    return [steam.header_image(app_id), *info.screenshots[:3]], genres


MAX_LOOT_PER_RUN = 1  # DLC й предмети з роздач: не більше одного за запуск, щоб не витісняли ігри


def run_free(ctx: Context) -> int:
    cfg = ctx.cfg
    tz = ZoneInfo(cfg.timezone)
    items = gamerpower.fetch_giveaways(ctx.session)
    try:  # DLC та внутрішньоігровий вміст: другорядне, тож його збій нічого не ламає
        known = {g.id for g in items}
        items += [g for g in gamerpower.fetch_giveaways(ctx.session, "loot") if g.id not in known]
    except Exception as exc:
        ctx.log(f"Роздачі: додатковий вміст недоступний ({type(exc).__name__}).")
    new = [g for g in items if not ctx.db.was_posted(f"free:{g.id}")]
    if not new:
        ctx.log("Роздачі: нових немає.")
        return 0
    first_run = ctx.db.count_kind("free") == 0
    new = [g for g in new if g.kind != "loot"] + [g for g in new if g.kind == "loot"][:MAX_LOOT_PER_RUN]  # ігри першими
    to_post = new[: cfg.max_free_per_run]
    if first_run and not cfg.dry_run:
        # Перший запуск: старі роздачі не вивалюємо в канал, лише запам'ятовуємо.
        for giveaway in new[cfg.max_free_per_run :]:
            ctx.db.mark_posted(f"free:{giveaway.id}", "free-skipped")
    posted = 0
    for giveaway in to_post:
        try:
            blurb = ai_blurb(
                ctx, "game", giveaway.title, giveaway.description,
                f"ai:free:{giveaway.id}", f"free:{giveaway.id}",
                alt_text=lambda g=giveaway: steam_description(ctx, g.title),
            )
        except DeferPost:
            continue
        posted += 1
        photos, genres = enrich_giveaway(ctx, giveaway)
        ctx.publish(
            "free",
            [f"free:{giveaway.id}"],
            posts.giveaway_post(giveaway, tz, blurb, cfg.signature, genres),
            photos,
        )
        if not cfg.dry_run:
            weekly.record_free(ctx.db, int(ctx.clock()), giveaway)
    ctx.log(f"Роздачі: опубліковано {posted}.")
    return posted


def apply_ua_titles(ctx: Context, episodes: list[anilist.Episode]) -> list[anilist.Episode]:
    """Українська назва: з вашого titles_ua.json, інакше з назв, які вже писали команди озвучки."""
    titles = load_titles(ctx.cfg.titles_path)
    learned = ctx.db.cache_items("ua-title:")
    out = []
    for e in episodes:
        ua = lookup_title(titles, e.media_id, e.title)
        if not ua and learned:
            names = (e.title, *e.alt_titles)
            ua = next(
                (v for k, v in learned.items() if any(titles_match(k, n) for n in names)), None
            )
        out.append(replace(e, ua_title=ua))
    return out


def run_anime(ctx: Context) -> int:
    cfg = ctx.cfg
    tz = ZoneInfo(cfg.timezone)
    now_ts = int(ctx.clock())
    today = datetime.fromtimestamp(now_ts, tz).date()
    key = f"anime-day:{today.isoformat()}"
    repeat = cfg.dry_run or cfg.force  # перегляд і force не зважають на «вже публікували»
    digest_done = not cfg.anime_digest or (ctx.db.was_posted(key) and not repeat)
    episodes = anilist.fetch_airing(ctx.session, now_ts, now_ts + 24 * 3600)
    episodes = apply_ua_titles(ctx, episodes)
    popular = [e for e in episodes if e.popularity >= cfg.anime_min_popularity]
    if not popular:
        ctx.log("Аніме: немає популярних серій у найближчі 24 години.")
        return 0
    top = sorted(popular, key=lambda e: e.popularity, reverse=True)[: cfg.anime_max_items]
    top.sort(key=lambda e: e.airing_at)
    result = 0
    if not digest_done:
        reported: set[int] = set()
        for e in top:  # підказка, що дописати у titles_ua.json
            if not e.ua_title and e.media_id not in reported:
                reported.add(e.media_id)
                ctx.log(f"Немає української назви: «{e.title}» (номер {e.media_id}) → {cfg.titles_path}")
        ctx.publish("anime", [key], posts.anime_digest(top, today, tz, cfg.signature))
        result = len(top)
    # Найпопулярніші серії отримують окремий пост з обкладинкою та описом. Картки, які
    # відклали через ШІ, допублікуємо наступним запуском (не більше anime_highlights на добу).
    now_dt = datetime.fromtimestamp(now_ts, timezone.utc)
    budget = cfg.anime_highlights
    if not repeat:
        budget -= ctx.db.count_since("anime-card", start_of_local_day(tz, now_dt))
    cards = 0
    for ep in sorted(top, key=lambda e: e.popularity, reverse=True)[: cfg.anime_highlights]:
        card_key = f"anime-card:{ep.media_id}:{ep.episode}"
        if cards >= budget or (not repeat and ctx.db.was_posted(card_key)):
            continue
        try:
            blurb = ai_blurb(
                ctx, "anime", ep.title, ep.description, f"ai:anime:{ep.media_id}", card_key,
                alt_text=lambda e=ep: mal_synopsis(ctx, e.mal_id),
            )
        except DeferPost:
            continue
        ctx.publish("anime-card", [card_key], posts.anime_card(ep, tz, now_ts, blurb, cfg.signature), ep.cover)
        cards += 1
    if digest_done and not cards:
        ctx.log("Аніме: нових карток немає (список «Аніме сьогодні» вимкнено або вже опубліковано).")
    else:
        ctx.log(f"Аніме: серій у виборці {len(top)}, карток: {cards}.")
    return result or cards


def find_media(ctx: Context, original: str, ua_title: str = "") -> anilist.AnimeInfo | None:
    """Аніме з AniList за оригінальною назвою (обкладинка, опис, жанри); результат і «не знайдено» кешуємо."""
    key = f"media:{norm_title(original)}"
    cached = ctx.db.get_cache(key)
    if cached is not None:
        if cached == "-":
            return None
        try:
            data = json.loads(cached)
            data["genres"] = tuple(data.get("genres") or ())
            return anilist.AnimeInfo(**data)
        except (ValueError, TypeError):
            pass  # зіпсований запис: шукаємо знову
    try:
        info = anilist.search_media(ctx.session, original)
    except Exception as exc:
        ctx.log(f"AniList: пошук аніме не вдався ({type(exc).__name__}).")
        return None
    if info is None and ctx.cfg.tmdb_api_key:  # не аніме: серіал у TMDB (українською), за оригіналом чи українською назвою
        try:
            info = tmdb.find_series(ctx.session, ctx.cfg.tmdb_api_key, original, ua_title)
        except Exception as exc:
            ctx.log(f"TMDB: пошук серіалу не вдався ({type(exc).__name__}).")
            return None
    if info is None:  # запасний варіант: TVMaze, лише точна назва
        try:
            info = tvmaze.search_show(ctx.session, original)
        except Exception as exc:
            ctx.log(f"TVMaze: пошук серіалу не вдався ({type(exc).__name__}).")
            return None
    ctx.db.set_cache(key, json.dumps(asdict(info), ensure_ascii=False) if info else "-")
    return info


_JUNK_TITLE = re.compile(
    r"\b(dlc|pack|bundle|pass|cosmetic|supporter|skin|chapter|currency|coins?)\b|доповнення|глава|набір",
    re.I,
)
SUBSCRIPTION_FRESH_DAYS = 3


def pick_console_deals(deals: list, cfg: Config, db: StateDB, now: datetime | None = None) -> list:
    """Лише гідні знижки на повноцінні ігри: без DLC, дрібниці й повторів; найвигідніші (у гривнях) першими."""
    best: dict[str, Any] = {}
    for d in deals:
        if d.ps_plus_only or d.price_new <= 0 or d.discount < cfg.min_discount:
            continue
        if d.price_old < cfg.console_min_price or _JUNK_TITLE.search(d.title):
            continue
        key = norm_title(base_title(d.title))
        if not key or db.was_posted(f"cdeal:{d.game_id}", cfg.deal_repeat_days, now) or db.was_posted(
            f"ctitle:{key}", cfg.deal_repeat_days, now
        ):
            continue
        saving = d.price_old - d.price_new
        if key not in best or saving > best[key].price_old - best[key].price_new:
            best[key] = d
    return sorted(best.values(), key=lambda d: d.price_old - d.price_new, reverse=True)


def pick_console_free(deals: list, db: StateDB) -> list:
    out, seen = [], set()
    for d in deals:
        key = norm_title(base_title(d.title))
        if not d.free or d.price_old <= 0 or not key or key in seen or db.was_posted(f"cfree:{d.game_id}"):
            continue
        seen.add(key)
        out.append(d)
    return out


_UA_EDITION = re.compile(r"[\s:\-–—]*(?:[\w']+\s+)?видання(?:\s+[\w']+)?\s*$", re.I)


def console_game_name(title: str) -> str:
    """Назва гри для пошуку у Steam: PSprices дає українські позначки («Exoprimal: видання Deluxe»), ще й у дужках платформи."""
    name = re.sub(r"\s*\([^)]*\)\s*$", "", title).strip()
    name = _UA_EDITION.sub("", name).strip(" :-–—") or name
    return base_title(name)


def console_photos(ctx: Context, deal: consoles.ConsoleDeal) -> list[str]:
    """Обкладинка консольного магазину + 2 скріншоти зі Steam, якщо гра є й там (ексклюзиви лишаються з однією картинкою)."""
    photos = [deal.image] if deal.image else []
    try:
        app_id = find_steam_app(ctx, console_game_name(deal.title))
        info = steam.fetch_app(ctx.session, app_id) if app_id else None
    except Exception as exc:
        ctx.log(f"Steam: скріншоти для консольної гри недоступні ({type(exc).__name__}).")
        return photos
    return photos + list(info.screenshots[:2]) if info and info.screenshots else photos


def run_consoles(ctx: Context) -> int:
    """PS Store / Microsoft Store: передплатні добірки (PS Plus, Game Pass), безкоштовне й найкращі знижки."""
    cfg = ctx.cfg
    posted = 0
    try:  # 1) офіційні оголошення про підписки
        now = datetime.fromtimestamp(ctx.clock(), tz=timezone.utc)
        for item in consoles.fetch_announcements(ctx.session):
            fresh = item.published is not None and (now - item.published).days < SUBSCRIPTION_FRESH_DAYS
            if not fresh or ctx.db.was_posted(f"sub:{item.url}"):
                continue
            ctx.publish("console-sub", [f"sub:{item.url}"], posts.subscription_post(item, cfg.signature), item.image)
            posted += 1
    except Exception as exc:
        ctx.log(f"Консолі: підписки недоступні ({type(exc).__name__}: {exc}).")
    deals = consoles.fetch_deals(ctx.session)
    for d in pick_console_free(deals, ctx.db)[:2]:
        ctx.publish("console-free", [f"cfree:{d.game_id}"], posts.console_free_post(d, cfg.signature), console_photos(ctx, d))
        posted += 1
    tz = ZoneInfo(cfg.timezone)
    budget = min(cfg.max_console_deals_per_run, cfg.max_console_deals_per_day - ctx.db.count_since("console-deal", start_of_local_day(tz)))
    for d in pick_console_deals(deals, cfg, ctx.db)[: max(budget, 0)]:
        key = norm_title(base_title(d.title))
        ctx.publish("console-deal", [f"cdeal:{d.game_id}", f"ctitle:{key}"], posts.console_deal_post(d, cfg.signature), console_photos(ctx, d))
        posted += 1
    ctx.log(f"Консолі: опубліковано {posted}.")
    return posted


def run_releases(ctx: Context) -> int:
    """Помітні релізи ігор (календар Вікіпедії + популярність у Steam): «Сьогодні вийшла…» і в понеділок «Релізи тижня»."""
    cfg = ctx.cfg
    tz = ZoneInfo(cfg.timezone)
    today = datetime.fromtimestamp(ctx.clock(), tz).date()
    if cfg.dry_run and os.environ.get("RELEASES_DATE"):  # лише для перегляду: «а що було б у цей день»
        today = releases.parse_day(os.environ["RELEASES_DATE"])
    try:
        items = releases.fetch(ctx.session, today.year)
        if today.month == 12:  # тиждень може сягати січня
            try:
                items += releases.fetch(ctx.session, today.year + 1)
            except Exception:
                pass
    except Exception as exc:
        ctx.log(f"Релізи: календар недоступний ({type(exc).__name__}: {exc}).")
        return 0
    try:
        popular = releases.steam_popular(ctx.session)
    except Exception as exc:
        ctx.log(f"Релізи: популярність у Steam недоступна ({type(exc).__name__}); беремо лише консольні ексклюзиви.")
        popular = set()
    posted = 0
    if today.weekday() == 0 and not ctx.db.was_posted(f"relweek:{today}"):
        end = today + timedelta(days=6)
        week = releases.week_releases(items, today, end, popular)
        if len(week) >= 2:
            rows = [(r.day, r.title, r.platforms) for r in week]
            ctx.publish("release-week", [f"relweek:{today}"], posts.release_week_post(rows, today, end, cfg.signature), None)
            posted += 1
    budget = cfg.max_releases_per_day - ctx.db.count_since("release", start_of_local_day(tz))
    for r in releases.today_releases(items, today, popular):
        if budget <= 0:
            break
        if ctx.db.was_posted(f"rel:{r.key}"):
            continue
        app_id = find_steam_app(ctx, r.title) if "ПК" in r.platforms else None
        info = None
        if app_id:
            try:
                info = steam.fetch_app(ctx.session, app_id)
            except Exception as exc:
                ctx.log(f"Steam: не вдалося отримати дані «{r.title}» ({type(exc).__name__}).")
        try:
            blurb = ai_blurb(ctx, "game", r.title, info.description if info else "", f"ai:rel:{r.key}", f"rel:{r.key}", can_skip=True)
        except DeferPost:
            continue
        if blurb is None and info and has_cyrillic(info.description):
            blurb = info.description
        genres = tuple(g for g in (info.genres if info else ()) if has_cyrillic(g))[:3]
        photos = [steam.header_image(app_id), *info.screenshots[:2]] if info and info.screenshots else ([steam.header_image(app_id)] if app_id else [])
        url = f"https://store.steampowered.com/app/{app_id}/" if app_id else ""
        text = posts.release_post(r.title, r.day, r.platforms, blurb, genres, info.price_final if info else "", url, cfg.signature)
        ctx.publish("release", [f"rel:{r.key}"], text, photos)
        posted += 1
        budget -= 1
    ctx.log(f"Релізи: опубліковано {posted}.")
    return posted


def run_pregen(ctx: Context) -> int:
    """Генерує описи наперед і складає їх у кеш, поки квота ключів є. Звичайні запуски потім беруть
    готовий текст і майже не звертаються до ШІ. Зупиняється, щойно ключі вичерпано."""
    cfg, editor = ctx.cfg, ctx.editor
    if editor is None:
        ctx.log("Генерація наперед: немає ключа ШІ.")
        return 0
    made = 0
    stopped = False

    def warm(kind: str, title: str, text: str, key: str) -> None:
        nonlocal made, stopped
        if stopped or not text or made >= cfg.pregen_max:
            return
        if editor.db.get_cache(f"v{PROMPT_VERSION}:{key}"):
            return  # уже є
        editor.blurb(kind, title, text, key)
        made += 1
        if editor.failed:
            stopped = True
            ctx.log("Генерація наперед: ШІ недоступний (ліміт чи помилка), зупиняюсь до наступного разу.")

    def safe(name: str, func: Callable[[], None]) -> None:
        try:
            func()
        except Exception as exc:  # одне джерело не зупиняє решту
            ctx.log(f"Генерація наперед: {name}: {type(exc).__name__}")

    def anime() -> None:
        now_ts = int(ctx.clock())
        episodes = anilist.fetch_airing(ctx.session, now_ts, now_ts + 48 * 3600)
        popular = sorted(
            (e for e in episodes if e.popularity >= cfg.anime_min_popularity),
            key=lambda e: e.popularity, reverse=True,
        )
        for ep in popular[: cfg.anime_highlights * 2]:
            warm("anime", ep.title, ep.description, f"ai:anime:{ep.media_id}")

    def dubs_media() -> None:
        originals: dict[str, str] = {}
        for raw in ctx.db.cache_items("dubpost3:").values():
            for r in dubs.releases_from_json(raw):
                if r.original:
                    originals.setdefault(norm_title(r.original), r.original)
        infos = [i for i in (find_media(ctx, o) for o in list(originals.values())[:20]) if i]
        for info in sorted(infos, key=lambda i: i.popularity, reverse=True)[:8]:
            warm("anime", info.title, info.description, f"ai:anime:{info.id}")

    def giveaways() -> None:
        for g in gamerpower.fetch_giveaways(ctx.session)[:6]:
            if not ctx.db.was_posted(f"free:{g.id}"):
                warm("game", g.title, g.description, f"ai:free:{g.id}")

    def deals() -> None:
        stores = cheapshark.fetch_stores(ctx.session)
        found = filter_trusted(cheapshark.fetch_deals(ctx.session), stores, cfg, ctx.log)
        for d in pick_deals(found, cfg, ctx.db)[:8]:
            if stopped or made >= cfg.pregen_max:
                break
            app_id = d.steam_app_id or find_steam_app(ctx, d.title)
            info = steam.fetch_app(ctx.session, app_id) if app_id else None
            if info:
                warm("game", d.title, info.description, f"ai:game:{app_id}")

    for name, func in (("аніме", anime), ("озвучка", dubs_media), ("роздачі", giveaways), ("знижки", deals)):
        safe(name, func)
    ctx.log(f"Генерація наперед: нових описів створено {made}.")
    return made


def run_compare(ctx: Context) -> int:
    """Той самий текст через Claude й Gemini поруч, у ваш приват: щоб самому побачити, чий опис кращий."""
    contenders = ctx.compare_editors or [("Claude", ctx.editor), ("Gemini", ctx.alt_editor)]
    contenders = [(label, ed) for label, ed in contenders if ed is not None]
    if len(contenders) < 2:
        ctx.send_preview("ℹ️ Для порівняння потрібні обидва ключі: ANTHROPIC_API_KEY і GEMINI_API_KEY.", [])
        return 0
    now_ts = int(ctx.clock())
    samples: list[tuple[str, str, str, str]] = []  # (вид, назва, текст, ідентифікатор)
    try:
        episodes = anilist.fetch_airing(ctx.session, now_ts, now_ts + 48 * 3600)
        for ep in sorted(episodes, key=lambda e: e.popularity, reverse=True)[:3]:
            if ep.description:
                samples.append(("anime", ep.title, ep.description, f"a{ep.media_id}"))
    except Exception as exc:
        ctx.log(f"Порівняння: аніме недоступне ({type(exc).__name__}).")
    try:
        for g in gamerpower.fetch_giveaways(ctx.session)[:2]:
            if g.description:
                samples.append(("game", g.title, g.description, f"g{g.id}"))
    except Exception as exc:
        ctx.log(f"Порівняння: роздачі недоступні ({type(exc).__name__}).")
    report: list[dict[str, str]] = []
    for kind, title, text, ident in samples[:5]:
        outs = []
        for label, editor in contenders:
            result = editor.blurb(kind, title, text, f"ai:cmp:{label}:{ident}")
            why = getattr(editor, "last_reason", "")
            report.append({"title": title, "provider": label, "ok": "yes" if result else "no", "text": result or "",
                           "reason": "" if result else (why or "невідомо")})
            outs.append(
                f"<b>{label}:</b> "
                + (posts.esc(result) if result else f"— (немає відповіді: {posts.esc(why or 'невідомо')})")
            )
        ctx.send_preview(f"<b>{posts.esc(title)}</b>\n\n" + "\n\n".join(outs), [])
    try:  # причини відмов поруч із кодом: їх видно без доступу до логів запуску
        out_dir = os.path.join(os.path.dirname(ctx.cfg.db_path) or ".", "probe")
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "compare.json"), "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass
    ctx.log(f"Порівняння: показано {len(samples[:5])} прикладів.")
    return len(samples[:5])


def find_cover(ctx: Context, original: str) -> str | None:
    info = find_media(ctx, original)
    return info.cover if info else None


def extract_dubs(ctx: Context, fresh: list, tz: ZoneInfo) -> dict | None:
    """Розбір дописів озвучки: просте завдання віддаємо Gemini (безкоштовний ліміт), а якщо він
    не впорався, пробуємо основного редактора. Тексти й описи пише лише основний (Claude)."""
    for editor in (ctx.extractor, ctx.editor):
        if editor is None:
            continue
        extracted = dubs.extract(editor, fresh, tz)
        if extracted is not None:
            return extracted
        if editor is ctx.editor:
            break
        ctx.log("Озвучка: розбір через Gemini не вдався, пробую основного редактора.")
    return None


def dub_tags(infos: Any) -> tuple[str, ...]:
    kinds = {i.kind for i in infos}
    tags = tuple(t for k, t in (("anime", "#аніме"), ("series", "#серіали")) if k in kinds)
    return tags or ("#аніме",)


def dub_lines_for(ctx: Context, names: set[str]) -> list[str]:
    """Рядки «🎧 Українською: команда, сезон, серія (сайт)» за вже розібраними дописами каналів озвучки."""
    best: dict[str, tuple[tuple[int, int], dubs.Release]] = {}
    for key, raw in ctx.db.cache_items("dubpost3:").items():
        channel = key.removeprefix("dubpost3:").rsplit(":", 1)[0]
        team = ctx.db.get_cache(f"dubteam:{channel}") or channel
        sites = dubs.load_channel_sites(ctx.cfg.dub_channels_path)
        for r in dubs.releases_from_json(raw):
            if not ({norm_title(r.title), norm_title(r.original or "")} & names):
                continue
            rank = (r.season or 1, r.episode or 0)
            current = best.get(team)
            if current is None or rank > current[0]:
                best[team] = (rank, replace(r, team=team, channel=channel, site=r.site or sites.get(channel.casefold())))
    lines = []
    for team, (_, r) in best.items():
        label = posts._episode_label(r)
        tail = f" ({posts.esc(r.site)})" if r.site and ctx.cfg.dub_sites else ""
        name = posts.esc(team)
        if ctx.cfg.dub_card_links and r.channel:
            name = f'<a href="https://t.me/{posts.esc(r.channel)}">{name}</a>'
        lines.append(f"🎧 Українською: {name}" + (f", {posts.esc(label)}" if label else "") + tail)
    return lines[:3]  # порожньо, якщо озвучки не знайшли: тоді про серіал не пишемо


def run_series(ctx: Context) -> int:
    """Нові серії популярних серіалів, що виходять просто зараз (TMDB), лише ті, що вже мають українську озвучку."""
    cfg = ctx.cfg
    if not cfg.tmdb_api_key:
        ctx.log("Серіали: потрібен ключ TMDB_API_KEY, пропускаю.")
        return 0
    tz = ZoneInfo(cfg.timezone)
    now = datetime.fromtimestamp(int(ctx.clock()), timezone.utc)
    budget = cfg.series_per_day if (cfg.dry_run or cfg.force) else cfg.series_per_day - ctx.db.count_since(
        "series", start_of_local_day(tz, now)
    )
    budget = min(budget, cfg.series_per_run)
    if budget <= 0:
        ctx.log("Серіали: на сьогодні ліміт вичерпано.")
        return 0
    items = [i for i in tmdb.fetch_airing_today(ctx.session, cfg.tmdb_api_key)
             if tmdb.is_candidate(i, cfg.series_min_popularity)]
    items.sort(key=lambda i: float(i.get("popularity") or 0), reverse=True)
    earliest = (now - timedelta(days=3)).date().isoformat()
    today_iso = now.date().isoformat()
    posted = 0
    report: list[dict] = []
    for item in items[:20]:
        if posted >= budget:
            break
        try:
            show = tmdb.fetch_airing_show(ctx.session, cfg.tmdb_api_key, int(item["id"]), today_iso)
        except Exception as exc:
            ctx.log(f"TMDB: подробиці серіалу не отримано ({type(exc).__name__}).")
            continue
        report.append({"id": item["id"], "popularity": item.get("popularity"),
                       "show": show.name if show else None, "air_date": show.air_date if show else None,
                       "episode": f"S{show.season}E{show.episode}" if show else None})
        if show is None or show.air_date < earliest:  # серія старіша за три доби: не новина
            continue
        key = f"series-ep:{show.id}:{show.season}:{show.episode}"
        if not (cfg.dry_run or cfg.force) and ctx.db.was_posted(key):
            continue
        try:
            blurb = ai_blurb(ctx, "series", show.name, show.overview, f"ai:series:{show.id}", key, can_skip=True)
        except DeferPost:
            continue
        names = {norm_title(show.name), norm_title(show.original_name)} - {""}
        dub_lines = dub_lines_for(ctx, names)
        if not dub_lines:  # без української озвучки така серія каналу про озвучку не потрібна
            continue
        text = posts.series_card(show, blurb, dub_lines, cfg.signature)
        ctx.publish("series", [key], text, show.poster)
        posted += 1
    if cfg.dry_run:  # перелік кандидатів лишаємо у файлі, щоб бачити, що знайшов TMDB, без доступу до логів
        try:
            os.makedirs("data/probe", exist_ok=True)
            with open("data/probe/series.json", "w", encoding="utf-8") as fh:
                json.dump({"candidates_found": len(items), "checked": report}, fh, ensure_ascii=False, indent=2)
        except OSError:
            pass
    ctx.log(f"Серіали: опубліковано {posted}.")
    return posted


def run_weekly(ctx: Context) -> int:
    """П'ятничні «Підсумки тижня»: раз на тиждень, у п'ятницю ввечері (dry-run і force ігнорують день і годину)."""
    cfg = ctx.cfg
    tz = ZoneInfo(cfg.timezone)
    now_ts = int(ctx.clock())
    key = weekly.week_key(now_ts, tz)
    manual = cfg.dry_run or cfg.force
    if not manual and (not weekly.is_time(now_ts, tz) or ctx.db.was_posted(key)):
        return 0
    text = weekly.build_text(weekly.load(ctx.db, now_ts), now_ts, tz, cfg.signature)
    if text is None:
        ctx.log("Підсумки тижня: за тиждень нічого збереженого, пропускаю.")
        return 0
    ctx.publish("weekly", [key], text)
    return 1


def run_dubs(ctx: Context) -> int:
    """Збирає з каналів команд озвучки факти «що вийшло / що очікується сьогодні»."""
    cfg = ctx.cfg
    channels = dubs.load_channels(cfg.dub_channels_path)
    if not channels:
        ctx.log(f"Озвучка: немає каналів у {cfg.dub_channels_path}.")
        return 0
    if ctx.editor is None:
        ctx.log("Озвучка: потрібен ключ ШІ (ANTHROPIC_API_KEY або GEMINI_API_KEY), пропускаю.")
        return 0
    tz = ZoneInfo(cfg.timezone)
    now = datetime.fromtimestamp(int(ctx.clock()), timezone.utc)
    since = now - timedelta(hours=cfg.dub_window_hours)
    found: list[dubs.Release] = []
    sites = dubs.load_channel_sites(cfg.dub_channels_path)
    stats: list[dict] = []
    for name, team_override in channels:
        try:
            channel = tgchannel.fetch_channel(ctx.session, name)
        except Exception as exc:  # один канал не повинен ламати решту
            ctx.log(f"Озвучка: канал {name} недоступний ({type(exc).__name__}).")
            continue
        team = team_override or dubs.clean_team(channel.title) or name
        ctx.db.set_cache(f"dubteam:{name}", team)  # потрібно, щоб казати, хто озвучив серіал
        recent = [p for p in channel.posts if p.date >= since and p.text]
        if not channel.posts:
            ctx.log(f"Озвучка: у {name} не видно дописів (веб-перегляд вимкнено?).")
        fresh = [p for p in recent if ctx.db.get_cache(f"dubpost3:{name}:{p.id}") is None]
        if fresh:
            extracted = extract_dubs(ctx, fresh, tz)
            if extracted is None:
                ctx.log(f"Озвучка: {team}: не вдалося розібрати {len(fresh)} дописів, спробую пізніше.")
            else:
                for post in fresh:
                    if post.id in extracted:  # лише розібрані; решту спробуємо в наступному запуску
                        ctx.db.set_cache(
                            f"dubpost3:{name}:{post.id}", dubs.releases_to_json(extracted[post.id])
                        )
        before = len(found)
        parsed = 0
        for post in recent:
            cached = ctx.db.get_cache(f"dubpost3:{name}:{post.id}")
            if cached is not None:
                parsed += 1
            if cached:
                found += dubs.with_team(dubs.releases_from_json(cached), team, name, sites.get(name.casefold()))
        stats.append({
            "channel": name, "recent": len(recent), "parsed": parsed, "new_to_parse": len(fresh),
            "releases": len(found) - before,
            "ai_error": " | ".join(
                filter(None, {getattr(e, "last_error", "") for e in (ctx.extractor, ctx.editor) if e})
            ),
        })
        ctx.log(f"Озвучка: {team}: дописів за добу {len(recent)}, нових для розбору {len(fresh)}.")
    if cfg.dry_run:  # у перегляді лишаємо поруч із кодом, як розібрано кожен канал (без доступу до логів запуску)
        try:
            os.makedirs("data/probe", exist_ok=True)
            with open("data/probe/dub_stats.json", "w", encoding="utf-8") as fh:
                json.dump(stats, fh, ensure_ascii=False, indent=2)
        except OSError:
            pass
    for r in found:  # українська назва + оригінал: запам'ятовуємо для розкладу аніме
        if r.original and has_cyrillic(r.title):
            ctx.db.set_cache(f"ua-title:{norm_title(r.original)}", r.title)
    today = now.astimezone(tz).date()
    was_posted = (lambda _key: False) if cfg.force else ctx.db.was_posted
    known: dict[str, anilist.AnimeInfo] = {}  # знайдені в AniList/TMDB/TVMaze: їм віддаємо перевагу в добірці
    for r in found[:60]:  # результати кешуються, тож повторні запуски майже не звертаються до джерел
        if r.ident not in known and (r.original or (cfg.tmdb_api_key and r.title)):
            info = find_media(ctx, r.original or r.title, r.title)
            if info:
                known[r.ident] = info

    def prefer(r: dubs.Release) -> int:
        info = known.get(r.ident)
        return 0 if not (info and info.cover) else (2 if info.kind == "series" else 1)

    recent = (lambda _key: False) if cfg.force else (lambda key: ctx.db.was_posted(key, within_days=1, now=now))
    released, upcoming = dubs.select_items(
        found, today, now.astimezone(tz), was_posted, cfg.dub_max_items, prefer, recent
    )
    if not released and not upcoming:
        ctx.log("Озвучка: нових релізів немає.")
        return 0
    infos: dict[str, anilist.AnimeInfo] = {}  # реліз -> аніме з AniList (для обкладинок і карток)
    for r in released + upcoming:
        if r.original or (cfg.tmdb_api_key and r.title):
            info = find_media(ctx, r.original or r.title, r.title)
            if info:
                infos[r.ident] = info
    if cfg.dry_run:  # у перегляді лишаємо поруч із кодом, що знайдено, щоб було видно без доступу до логів
        try:
            os.makedirs("data/probe", exist_ok=True)
            report, seen_ids = [], set()
            for r in found[:80]:
                if r.ident in seen_ids:
                    continue
                seen_ids.add(r.ident)
                look = infos.get(r.ident) or (
                    find_media(ctx, r.original or r.title, r.title) if (r.original or cfg.tmdb_api_key) else None
                )
                report.append({
                    "team": r.team, "title": r.title, "season": r.season, "episode": r.episode, "status": r.status,
                    "original": r.original, "site": r.site, "premium": r.premium,
                    "selected": r in released or r in upcoming,
                    "found": look.kind if look else None, "matched": look.title if look else None,
                    "cover": bool(look and look.cover), "has_description": bool(look and look.description),
                })
            with open("data/probe/dubs.json", "w", encoding="utf-8") as fh:
                json.dump(report, fh, ensure_ascii=False, indent=2)
        except OSError:
            pass
    has_covers = any(i.cover for i in infos.values())
    if has_covers:  # підпис до альбому обмежений 1024 символами: зайве піде в наступну добірку
        text, released, upcoming = posts.dub_digest_fit(
            released, upcoming, today, cfg.signature, cfg.dub_links, tags=dub_tags(infos.values())
        )
    else:
        text = posts.dub_digest(released, upcoming, today, cfg.signature, cfg.dub_links)
    covers: list[str] = []
    for r in released + upcoming:
        info = infos.get(r.ident)
        if info and info.cover and info.cover not in covers and len(covers) < MAX_DUB_COVERS:
            covers.append(info.cover)
    ctx.publish("dub", dubs.keys_for(released, upcoming), text, covers)
    if not cfg.dry_run:
        for r in released:
            weekly.record_dub(ctx.db, int(ctx.clock()), r)
    # Найпопулярніші релізи отримують окрему картку з обкладинкою й описом (як картки аніме).
    repeat = cfg.dry_run or cfg.force
    shown = sorted(
        (r for r in released + upcoming if r.ident in infos and infos[r.ident].cover),
        key=lambda r: infos[r.ident].popularity,
        reverse=True,
    )
    first_series = next((r for r in shown if infos[r.ident].kind == "series"), None)
    if first_series is not None:  # одна картка завжди для серіалу, якщо він є: інакше аніме витісняє їх усі
        shown = [first_series] + [r for r in shown if r is not first_series]
    cards = 0
    ready: list[tuple] = []
    card_titles: list[str] = []
    card_shows: set[str] = set()
    for r in shown:
        if cards >= cfg.dub_highlights:
            break
        info = infos[r.ident]
        card_key = f"dub-card:{r.ident}"
        show_card = f"dub-cardshow:{info.kind}:{info.id}"  # одна картка на серіал, хоч би скільки команд його озвучили
        if show_card in card_shows or (
            not repeat and (ctx.db.was_posted(card_key) or ctx.db.was_posted(show_card, within_days=2, now=now))
        ):
            continue
        blurb = ai_blurb(
            ctx, "series" if info.kind == "series" else "anime", info.title or r.title, info.description,
            f"ai:series:{info.id}" if info.kind == "series" else f"ai:anime:{info.id}",
            card_key, can_skip=True, alt_text=lambda i=info: mal_synopsis(ctx, i.mal_id),
        )
        if not blurb:  # без опису картка слабка: у добірці цей реліз усе одно є
            continue
        ready.append((r, info, blurb, card_key, show_card))
        card_shows.add(show_card)
        cards += 1
    # Публікуємо блоками: спочатку картки аніме, потім серіалів (порядок популярності всередині блоку зберігається)
    ready.sort(key=lambda item: item[1].kind == "series")
    for r, info, blurb, card_key, show_card in ready:
        ctx.publish(
            "dub-card", [card_key, show_card],
            posts.dub_card(r, info, blurb, cfg.signature, cfg.dub_card_links, cfg.dub_sites), info.cover,
        )
        card_titles.append(f"{info.kind}: {r.title}")
    if cfg.dry_run:
        try:
            os.makedirs("data/probe", exist_ok=True)
            with open("data/probe/dub_cards.json", "w", encoding="utf-8") as fh:
                json.dump(card_titles, fh, ensure_ascii=False, indent=2)
        except OSError:
            pass
    ctx.log(
        f"Озвучка: у добірці вийшло {len(released)}, очікується {len(upcoming)}, карток: {cards}."
    )
    return len(released) + len(upcoming)


def sample_deal(ctx: Context) -> int:
    """Живі знижки для перегляду, без перевірки «вже публікували»: серія (якщо є) і окрема гра."""
    cfg = ctx.cfg
    stores = cheapshark.fetch_stores(ctx.session)
    deals = filter_trusted(cheapshark.fetch_deals(ctx.session), stores, cfg, ctx.log)
    best: dict[str, cheapshark.Deal] = {}
    for d in deals:
        if d.sale_price > 0 and d.savings >= cfg.min_discount and d.rating >= cfg.min_deal_rating:
            cur = best.get(d.title.casefold())
            if cur is None or (d.rating, d.savings) > (cur.rating, cur.savings):
                best[d.title.casefold()] = d
    good = sorted(best.values(), key=lambda d: (d.rating, d.savings), reverse=True)
    if not good:
        return 0
    groups = group_deals(good)
    series = [g for g in groups if len(g) > 1]
    chosen: list[list[cheapshark.Deal]] = []
    if series:
        chosen.append(max(series, key=lambda g: (len(g), g[0].rating)))
    single = max(
        (d for d in good if d.steam_app_id is not None), key=lambda d: (d.rating, d.savings),
        default=good[0],
    )
    chosen.append([single])
    shown = 0
    for group in chosen:
        store = stores.get(group[0].store_id, "")
        built = deal_post_text(ctx, group, store)
        if built is None:
            continue
        text, photos, trailer = built
        ctx.publish("deal", deal_keys(group), text, photos, trailer)
        shown += 1
    return shown


def sample_free(ctx: Context) -> int:
    items = gamerpower.fetch_giveaways(ctx.session)
    if not items:
        return 0
    giveaway = items[0]
    blurb = None
    if ctx.editor and giveaway.description:
        blurb = ctx.editor.blurb("game", giveaway.title, giveaway.description, f"ai:free:{giveaway.id}")
    photos, genres = enrich_giveaway(ctx, giveaway)
    ctx.publish(
        "free", [f"free:{giveaway.id}"],
        posts.giveaway_post(giveaway, ZoneInfo(ctx.cfg.timezone), blurb, ctx.cfg.signature, genres),
        photos,
    )
    return 1


def sample_anime(ctx: Context) -> int:
    """Жива добірка й дві картки з обкладинками; якщо популярних немає, беремо найпопулярніші."""
    cfg = ctx.cfg
    tz = ZoneInfo(cfg.timezone)
    now_ts = int(ctx.clock())
    episodes = apply_ua_titles(ctx, anilist.fetch_airing(ctx.session, now_ts, now_ts + 24 * 3600))
    if not episodes:
        return 0
    top = sorted(episodes, key=lambda e: e.popularity, reverse=True)[: cfg.anime_max_items]
    top.sort(key=lambda e: e.airing_at)
    today = datetime.fromtimestamp(now_ts, tz).date()
    if cfg.anime_digest:
        ctx.publish("anime", ["sample"], posts.anime_digest(top, today, tz, cfg.signature))
    for ep in sorted(top, key=lambda e: e.popularity, reverse=True)[:2]:
        blurb = None
        if ctx.editor and ep.description:
            blurb = ctx.editor.blurb("anime", ep.title, ep.description, f"ai:anime:{ep.media_id}")
        ctx.publish(
            "anime-card", ["sample"], posts.anime_card(ep, tz, now_ts, blurb, cfg.signature), ep.cover
        )
    return len(top)


def ai_status(ctx: Context) -> str:
    """Чесний стан ШІ-редактора: підключено, чи працює запит, що не так."""
    editor = ctx.editor
    if editor is None:
        return (
            "⚠️ ШІ-редактор вимкнено: до запуску не дійшов жоден ключ "
            "(GEMINI_API_KEY або ANTHROPIC_API_KEY). Через це немає описів аніме, "
            "перекладу описів ігор і розбору анонсів озвучки."
        )
    notes: list[str] = []
    saved, editor.log = editor.log, notes.append
    try:
        answer = editor.ask("Відповідай одним словом.", "Напиши слово: ок", 20)
    finally:
        editor.log = saved
    name = "Gemini" if editor.provider == "gemini" else "Claude"
    keys = f", ключів: {len(editor.keys)}" if editor.provider == "gemini" else ""
    if answer:
        return f"🤖 ШІ-редактор працює ({name}{keys}, модель {editor.active_model})."
    reason = " ".join(dict.fromkeys(notes)) if notes else "без відповіді"
    return f"⚠️ ШІ-редактор підключено ({name}{keys}), але запит не вдався: {reason}"


SITES_PROBE = (
    "https://psprices.com/region-ua/rss/discounts/",
    "https://blog.playstation.com/tag/playstation-plus/feed/",
    "https://blog.playstation.com/category/ps-plus/feed/",
    "https://news.xbox.com/en-us/xbox-game-pass/feed/",
    "https://news.xbox.com/en-us/tag/game-pass/feed/",
    "https://www.dekudeals.com/hottest",
)
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)


def run_sites_probe(ctx: Context) -> int:
    """Чи доступні з GitHub Actions сайти з озвучкою, і як на них виглядає список нових серій.
    Лише читає по одній сторінці й лишає початок HTML у data/probe/sites, щоб написати розбір під кожен сайт."""
    out_dir = os.path.join(os.path.dirname(ctx.cfg.db_path) or ".", "probe", "sites")
    os.makedirs(out_dir, exist_ok=True)
    report = []
    for url in SITES_PROBE:
        host = re.sub(r"[^a-z0-9.]+", "_", url.split("://", 1)[1]).strip("_")[:80]
        entry: dict = {"url": url}
        try:
            resp = ctx.session.get(url, timeout=25)
            entry.update(status=resp.status_code, bytes=len(resp.content or b""))
            if resp.status_code != 200:  # те саме зі звичайними заголовками браузера (без обходу захисту)
                alt = ctx.session.get(url, timeout=25, headers={"User-Agent": BROWSER_UA, "Accept-Language": "uk"})
                entry.update(browser_status=alt.status_code)
                if alt.status_code == 200:
                    resp = alt
            if resp.status_code == 200:
                with open(os.path.join(out_dir, f"{host}.html"), "w", encoding="utf-8") as fh:
                    fh.write(resp.text[:150000])
        except Exception as exc:
            entry.update(error=type(exc).__name__)
        report.append(entry)
    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    consoles: dict = {}
    for platform in ("ps4", "ps5", "xbox-one", "xbox-series-xs", "switch"):  # що GamerPower віддає для консолей
        try:
            resp = ctx.session.get(gamerpower.API, params={"platform": platform, "sort-by": "date"}, timeout=25)
            data = resp.json() if resp.status_code == 200 else []
            consoles[platform] = {
                "status": resp.status_code,
                "items": [
                    {k: it.get(k) for k in ("id", "title", "type", "platforms", "worth", "end_date")}
                    for it in (data if isinstance(data, list) else [])[:12]
                ],
            }
        except Exception as exc:
            consoles[platform] = {"error": type(exc).__name__}
    with open(os.path.join(out_dir, "consoles.json"), "w", encoding="utf-8") as fh:
        json.dump(consoles, fh, ensure_ascii=False, indent=2)
    ctx.log("Сайти: " + "; ".join(f"{e['url']} -> {e.get('status') or e.get('error')}" for e in report))
    return len(report)


def run_probe_task(ctx: Context) -> int:
    results = probe.run_probe(ctx.session, ctx.cfg.itad_api_key)
    summary = ", ".join(str(r.get("status") or r.get("error")) for r in results)
    ctx.log(f"Проба ITAD: збережено {len(results)} відповідей у {probe.OUT} (статуси: {summary}).")
    ctx.send_preview(f"🔎 Проба IsThereAnyDeal завершена. Статуси: {summary}.", [])
    return len(results)


def tmdb_status(ctx: Context) -> str:
    """Чесний стан TMDB: ключ є й працює чи ні (без показу самого ключа)."""
    key = ctx.cfg.tmdb_api_key
    if not key:
        return "ℹ️ TMDB: ключ не підключено (TMDB_API_KEY), серіали шукаються лише в TVMaze."
    try:
        name = tmdb.check_key(ctx.session, key)
    except Exception as exc:
        code = getattr(getattr(exc, "response", None), "status_code", None)
        return f"⚠️ TMDB: ключ не спрацював ({type(exc).__name__}{', код ' + str(code) if code else ''})."
    return "✅ TMDB підключено, ключ працює." if name else "⚠️ TMDB відповів без результатів."


def run_test(ctx: Context) -> int:
    """Із приватним чатом перегляду шле вам живі приклади всіх постів; без нього перевіряє зв'язок із каналом."""
    if ctx.preview is None:
        text = "✅ Бот підключено. Канал «Лут і Серія» готовий до публікацій."
        if ctx.cfg.dry_run:
            ctx.log(f"DRY RUN: {text}")
        else:
            ctx.send(text, [])
            ctx.log("Тестове повідомлення надіслано.")
        return 1
    ctx.send_preview("✅ Бот підключено. Нижче живі приклади з реальних джерел. У канал нічого не йде.", [])
    ctx.send_preview(ai_status(ctx), [])
    status = tmdb_status(ctx)
    ctx.send_preview(status, [])
    try:  # стан ключа поруч із кодом, щоб його було видно без доступу до логів запуску
        os.makedirs("data/probe", exist_ok=True)
        with open("data/probe/tmdb.json", "w", encoding="utf-8") as fh:
            json.dump({"status": re.sub(r"<[^>]+>", "", status)}, fh, ensure_ascii=False)
    except OSError:
        pass
    sample = replace(ctx, cfg=replace(ctx.cfg, dry_run=True, force=True))
    shown = 0
    for name, func in (
        ("Знижка", sample_deal),
        ("Роздача", sample_free),
        ("Аніме", sample_anime),
        ("Озвучка", run_dubs),
        ("Серіали", run_series),
    ):
        try:
            count = func(sample)
        except Exception as exc:  # одна секція не повинна ховати решту
            ctx.log(f"Тест: {name}: {type(exc).__name__}: {exc}")
            ctx.send_preview(f"⚠️ {name}: не вдалося отримати дані ({type(exc).__name__}).", [])
            continue
        if count:
            shown += 1
        else:
            ctx.send_preview(f"ℹ️ {name}: зараз нічого показати (див. журнал запуску).", [])
    ctx.log(f"Тест: у приват надіслано приклади для {shown} з 5 розділів.")
    return shown


def check_channels(ctx: Context) -> tuple[list[dict], str]:
    """Чи читається кожен канал озвучки через t.me/s: список результатів і текст звіту для вас."""
    now = datetime.fromtimestamp(int(ctx.clock()), timezone.utc)
    tz = ZoneInfo(ctx.cfg.timezone)
    results: list[dict] = []
    lines = ["📡 <b>Канали озвучки: чи бачить їх бот</b>", ""]
    for name, team_override in dubs.load_channels(ctx.cfg.dub_channels_path):
        entry: dict = {"channel": name, "checked": now.isoformat(timespec="seconds")}
        try:
            channel = tgchannel.fetch_channel(ctx.session, name)
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            entry.update(ok=False, error=type(exc).__name__, http=status)
            lines.append(f"❌ {name}: не вдалося відкрити ({type(exc).__name__}{', код ' + str(status) if status else ''})")
        else:
            last = max((p.date for p in channel.posts), default=None)
            day = sum(1 for p in channel.posts if (now - p.date).total_seconds() <= 30 * 3600)
            team = team_override or dubs.clean_team(channel.title) or name
            entry.update(
                ok=True, title=channel.title, team=team, posts=len(channel.posts),
                posts_last_30h=day, last_post=last.isoformat(timespec="minutes") if last else None,
                # короткі уривки останніх дописів: видно, як канал пише про релізи (чи вказує сайт, серію тощо)
                samples=[re.sub(r"\s+", " ", p.text)[:160] for p in sorted(channel.posts, key=lambda x: x.date)[-4:]],
            )
            if not channel.posts:
                lines.append(f"⚠️ {name}: дописів не видно (веб-перегляд вимкнено або канал порожній)")
            else:
                when = last.astimezone(tz).strftime("%d.%m %H:%M")
                lines.append(
                    f"✅ {name} ({posts.esc(team)}): дописів {len(channel.posts)}, за 30 год {day}, останній {when}"
                )
        results.append(entry)
    if not results:
        lines.append(f"Список порожній: додайте канали в {ctx.cfg.dub_channels_path}.")
    return results, "\n".join(lines)


def run_channels(ctx: Context) -> int:
    results, report = check_channels(ctx)
    os.makedirs(os.path.dirname(CHANNELS_OUT), exist_ok=True)
    with open(CHANNELS_OUT, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)
    ctx.log(report)
    ctx.send_preview(report, [])
    return len(results)


TASKS: dict[str, Callable[[Context], int]] = {
    # Порядок = блоки в каналі: спершу аніме, потім серіали, потім ігри (без «вінегрету»)
    "anime": run_anime,
    "dubs": run_dubs,
    "series": run_series,
    "releases": run_releases,
    "free": run_free,
    "deals": run_deals,
    "consoles": run_consoles,
    "weekly": run_weekly,
}


# Блоки за часом доби: ранок = аніме, середина дня = ігри, вечір = серіали. Добірка озвучки виходить 4 рази на день.
GROUPS: dict[str, list[str]] = {
    "morning": ["anime", "dubs"],
    "games": ["free", "deals", "consoles", "releases"],
    "evening": ["series", "dubs", "weekly"],
}


def run_task(ctx: Context, task: str) -> list[str]:
    """Виконує завдання; повертає список помилок (порожній, якщо все добре)."""
    if task in ("test", "probe", "channels", "pregen", "compare", "sites", "setup", "avatar"):
        names = [task]
        funcs = {
            "setup": run_setup, "avatar": run_avatar, "sites": run_sites_probe, "test": run_test, "probe": run_probe_task, "channels": run_channels,
            "pregen": run_pregen, "compare": run_compare,
        }
    elif task == "all":
        names = list(TASKS)
        funcs = TASKS
    elif task in GROUPS:
        names = GROUPS[task]
        funcs = TASKS
    else:
        names = [task]
        funcs = TASKS
    errors: list[str] = []
    if ctx.cfg.dry_run and task != "test":
        ctx.send_preview("🧪 Перегляд: так виглядатимуть пости. У канал нічого не надіслано.", [])
    for name in names:
        try:
            funcs[name](ctx)
        except Exception as exc:  # одне джерело не повинно ламати решту
            message = f"{name}: {type(exc).__name__}: {exc}"
            ctx.log(f"ПОМИЛКА {message}")
            errors.append(message)
    return errors


def build_context(cfg: Config) -> Context:
    """Збирає все потрібне для запуску: сесію, базу, ШІ-редакторів і відправників."""
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    db = StateDB(cfg.db_path)
    telegram.SHARE = telegram.share_markup(cfg.signature) if cfg.share_button else None

    def send(text: str, photos: list[str]) -> None:
        telegram.send_post(session, cfg.bot_token, cfg.chat_id, text, photos)

    def send_video(text: str, video: str, photos: list[str]) -> None:
        telegram.send_video_post(session, cfg.bot_token, cfg.chat_id, text, video, photos)

    def claude_editor() -> Editor | None:
        return Editor(session, cfg.anthropic_api_key, cfg.ai_model, db) if cfg.anthropic_api_key else None

    def gemini_editor() -> Editor | None:
        # Якщо перший ключ Gemini порожній, а другий заданий, працюємо з другим
        gemini_keys = [k for k in (cfg.gemini_api_key, *cfg.gemini_extra_keys) if k]
        if not gemini_keys:
            return None
        return Editor(
            session,
            gemini_keys[0],
            cfg.gemini_model,
            db,
            provider="gemini",
            extra_keys=tuple(gemini_keys[1:]),
            rpm=cfg.gemini_rpm,
            fallback_models=cfg.gemini_fallback_models,
        )

    claude, gemini = claude_editor(), gemini_editor()
    # Тексти пише Claude (або Gemini, якщо TEXT_PROVIDER=gemini), анонси озвучки розбирає Gemini
    if cfg.text_provider == "gemini" and gemini:
        editor, alt_editor = gemini, claude
    else:
        editor, alt_editor = claude or gemini, (gemini if (claude and gemini) else None)
    extractor = gemini if (claude and gemini) else None
    compare_editors: list[tuple[str, Any]] = []
    if claude:
        compare_editors.append((f"Claude ({cfg.ai_model})", claude))
        if cfg.compare_model != cfg.ai_model:
            compare_editors.append(
                (f"Claude ({cfg.compare_model})", Editor(session, cfg.anthropic_api_key, cfg.compare_model, db))
            )
    if gemini:
        compare_editors.append((f"Gemini ({cfg.gemini_model})", gemini))
    preview = preview_video = None
    if cfg.preview_chat_id and cfg.bot_token and not cfg.quiet:
        preview = lambda text, photos: telegram.send_post(  # noqa: E731
            session, cfg.bot_token, cfg.preview_chat_id, text, photos
        )
        preview_video = lambda text, video, photos: telegram.send_video_post(  # noqa: E731
            session, cfg.bot_token, cfg.preview_chat_id, text, video, photos
        )

    ctx = Context(
        cfg=cfg, session=session, db=db, send=send, editor=editor, alt_editor=alt_editor, extractor=extractor, compare_editors=compare_editors, preview=preview,
        send_video=send_video, preview_video=preview_video,
    )
    return ctx


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lutserii", description="Бот каналу «Лут і Серія»")
    parser.add_argument("task", choices=["all", "morning", "games", "evening", "deals", "free", "anime", "dubs", "series", "consoles", "releases", "weekly", "test", "probe", "channels", "pregen", "compare", "sites", "setup", "avatar"])
    parser.add_argument("--dry-run", action="store_true", help="лише показати пости, не слати")
    args = parser.parse_args(argv)

    cfg = Config.from_env(dry_run=args.dry_run)
    if not cfg.dry_run and not (cfg.bot_token and cfg.chat_id):
        print("Не задано TELEGRAM_BOT_TOKEN або TELEGRAM_CHAT_ID.", file=sys.stderr)
        return 2

    ctx = build_context(cfg)
    db = ctx.db
    try:
        errors = run_task(ctx, args.task)
    finally:
        db.close()
    return 1 if errors else 0
