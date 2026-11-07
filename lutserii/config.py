"""Налаштування з змінних середовища."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else default


DEFAULT_TRUSTED_STORES = (
    "Steam",
    "GOG",
    "Epic Games Store",
    "Humble Store",
    "Fanatical",
    "GreenManGaming",
    "Gamesplanet",
)


def _env_list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class Config:
    bot_token: str = ""
    chat_id: str = ""
    db_path: str = "data/state.db"
    dry_run: bool = False
    quiet: bool = False  # QUIET=1: нічого не слати й у приват (лише діагностика у файлах data/probe)
    force: bool = False  # повторно опублікувати добірку аніме, навіть якщо за сьогодні вже була
    timezone: str = "Europe/Kyiv"
    # Знижки
    min_discount: int = 50
    min_deal_rating: float = 7.0
    max_deals_per_run: int = 4
    max_deals_per_day: int = 12
    deal_repeat_days: int = 14
    # Лише офіційні магазини (назви як у CheapShark). Порожній список = без фільтра.
    trusted_stores: tuple[str, ...] = DEFAULT_TRUSTED_STORES
    # Роздачі
    max_free_per_run: int = 4
    # Консолі (PS Store / Microsoft Store через PSprices, підписки PS Plus і Game Pass)
    max_console_deals_per_run: int = 3
    max_console_deals_per_day: int = 9
    console_min_price: float = 500.0  # ціна до знижки, ₴: дешеву дрібницю не постимо
    max_releases_per_day: int = 2  # пости «Сьогодні вийшла…» про помітні релізи ігор
    # Аніме
    anime_min_popularity: int = 20000
    anime_max_items: int = 15
    titles_path: str = "data/titles_ua.json"
    anime_highlights: int = 3  # скільки найпопулярніших серій отримують окремий пост із обкладинкою
    game_video: bool = True  # пости про ігри з трейлером зі Steam (відео), якщо він є; інакше альбом скріншотів
    series_per_run: int = 3  # нових серій популярних серіалів за один запуск
    series_per_day: int = 5
    series_min_popularity: float = 40.0  # поріг популярності TMDB для «Серіали сьогодні»
    anime_digest: bool = False  # щоденний список «Аніме сьогодні» (оригінал, Японія); за замовчуванням вимкнено
    # ШІ-редактор (необов'язково): без ключа пости виходять без ШІ-описів
    anthropic_api_key: str = ""
    ai_model: str = "claude-haiku-5-5"
    text_provider: str = "auto"  # хто пише описи: auto/claude (Claude, якщо є ключ) або gemini
    compare_model: str = "claude-sonnet-5-5"  # третій учасник порівняння task=compare
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"
    gemini_fallback_models: tuple[str, ...] = ("gemini-3.5-flash", "gemini-3.5-flash-lite")
    gemini_extra_keys: tuple[str, ...] = ()  # запасні ключі: беруться, коли в попереднього ліміт
    gemini_rpm: int = 5  # запитів на хвилину на один ключ (безкоштовний рівень)
    pregen_max: int = 12  # скільки нових описів ШІ створює за один запуск «генерації наперед»
    tmdb_api_key: str = ""  # TMDB: серіали (український опис і постер); без ключа працює лише TVMaze
    itad_api_key: str = ""  # IsThereAnyDeal: поки лише для пробних запитів (task=probe)
    # Чат, куди шлемо перегляд (dry-run) і приклади постів, щоб не публікувати їх у каналі
    preview_chat_id: str = ""
    # Озвучка українською: канали команд, вікно пошуку (години) і ліміт рядків у пості
    dub_channels_path: str = "data/dub_channels.txt"
    dub_window_hours: int = 30
    dub_max_items: int = 15
    dub_highlights: int = 3  # скільки популярних релізів озвучки отримують окрему картку з описом
    dub_links: bool = True  # у добірці озвучки назва команди — посилання на її Telegram-канал
    dub_sites: bool = True  # у картці релізу: назва сайту (без посилання), якщо допис її називає
    share_button: bool = True  # кнопка «Поділитись з другом» під постами (крім альбомів)
    dub_card_links: bool = True  # у картці релізу назва команди — посилання на її Telegram-канал

    @property
    def signature(self) -> str:
        """Підпис каналу в кінці поста (для публічного каналу це його @username)."""
        return self.chat_id if self.chat_id.startswith("@") else ""

    @classmethod
    def from_env(cls, dry_run: bool = False) -> "Config":
        env_dry = os.environ.get("DRY_RUN", "").strip().lower() in ("1", "true", "yes")
        return cls(
            bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
            chat_id=os.environ.get("TELEGRAM_CHAT_ID", "").strip(),
            db_path=os.environ.get("DB_PATH", "data/state.db"),
            dry_run=dry_run or env_dry,
            quiet=os.environ.get("QUIET", "").strip().lower() in ("1", "true", "yes"),
            force=os.environ.get("FORCE", "").strip().lower() in ("1", "true", "yes"),
            timezone=os.environ.get("TIMEZONE", "Europe/Kyiv"),
            min_discount=_env_int("MIN_DISCOUNT", 50),
            min_deal_rating=_env_float("MIN_DEAL_RATING", 7.0),
            max_deals_per_run=_env_int("MAX_DEALS_PER_RUN", 4),
            max_deals_per_day=_env_int("MAX_DEALS_PER_DAY", 12),
            deal_repeat_days=_env_int("DEAL_REPEAT_DAYS", 14),
            trusted_stores=_env_list("TRUSTED_STORES", DEFAULT_TRUSTED_STORES),
            max_free_per_run=_env_int("MAX_FREE_PER_RUN", 4),
            max_console_deals_per_run=_env_int("MAX_CONSOLE_DEALS_PER_RUN", 3),
            max_console_deals_per_day=_env_int("MAX_CONSOLE_DEALS_PER_DAY", 9),
            console_min_price=_env_float("CONSOLE_MIN_PRICE", 500.0),
            max_releases_per_day=_env_int("MAX_RELEASES_PER_DAY", 2),
            anime_min_popularity=_env_int("ANIME_MIN_POPULARITY", 20000),
            anime_max_items=_env_int("ANIME_MAX_ITEMS", 15),
            anime_highlights=_env_int("ANIME_HIGHLIGHTS", 3),
            game_video=os.environ.get("GAME_VIDEO", "1").strip().lower() not in ("0", "false", "no", "off"),
            series_per_run=_env_int("SERIES_PER_RUN", 3),
            series_per_day=_env_int("SERIES_PER_DAY", 5),
            series_min_popularity=_env_float("SERIES_MIN_POPULARITY", 40.0),
            anime_digest=os.environ.get("ANIME_DIGEST", "0").strip().lower() in ("1", "true", "yes", "on"),
            titles_path=os.environ.get("TITLES_PATH", "data/titles_ua.json"),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", "").strip(),
            ai_model=os.environ.get("AI_MODEL", "").strip() or "claude-haiku-5-5",
            text_provider=os.environ.get("TEXT_PROVIDER", "auto").strip().lower() or "auto",
            compare_model=os.environ.get("COMPARE_MODEL", "").strip() or "claude-sonnet-5-5",
            gemini_api_key=os.environ.get("GEMINI_API_KEY", "").strip(),
            gemini_model=os.environ.get("GEMINI_MODEL", "").strip() or "gemini-3.6-flash",
            gemini_extra_keys=tuple(
                k
                for k in (
                    os.environ.get("GEMINI_API_KEY_2", "").strip(),
                    os.environ.get("GEMINI_API_KEY_3", "").strip(),
                )
                if k
            ),
            gemini_rpm=_env_int("GEMINI_RPM", 5),
            gemini_fallback_models=_env_list(
                "GEMINI_FALLBACK_MODELS", ("gemini-3.5-flash", "gemini-3.5-flash-lite")
            ),
            pregen_max=_env_int("AI_PREGEN_MAX", 12),
            tmdb_api_key=os.environ.get("TMDB_API_KEY", "").strip(),
            itad_api_key=os.environ.get("ITAD_API_KEY", "").strip(),
            preview_chat_id=os.environ.get("PREVIEW_CHAT_ID", "").strip(),
            dub_channels_path=os.environ.get("DUB_CHANNELS_PATH", "data/dub_channels.txt"),
            dub_window_hours=_env_int("DUB_WINDOW_HOURS", 30),
            dub_max_items=_env_int("DUB_MAX_ITEMS", 15),
            dub_highlights=_env_int("DUB_HIGHLIGHTS", 3),
            dub_sites=os.environ.get("DUB_SITES", "1").strip().lower() not in ("0", "false", "no", "off"),
            share_button=os.environ.get("SHARE_BUTTON", "1").strip().lower() not in ("0", "false", "no", "off"),
            dub_card_links=os.environ.get("DUB_CARD_LINKS", "1").strip().lower() not in ("0", "false", "no", "off"),
            dub_links=os.environ.get("DUB_LINKS", "1").strip().lower() not in ("0", "false", "no", "off"),
        )
