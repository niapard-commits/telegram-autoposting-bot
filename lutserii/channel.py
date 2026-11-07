"""Налаштування самого каналу: опис і закріплений вітальний пост (завдання setup)."""

from __future__ import annotations

from typing import Any

from . import telegram

DESCRIPTION = (
    "Знижки та безкоштовні роздачі ігор для ПК, PlayStation і Xbox, нові серії аніме й серіалів "
    "з українською озвучкою. Щодня, без спаму."
)

PINNED_TEXT = (
    "👋 <b>Лут і Серія</b>\n\n"
    "Канал для тих, хто любить ігри, аніме й серіали, а переплачувати чи шукати не хоче.\n\n"
    "🔥 <b>Знижки.</b> ПК, PlayStation і Xbox: лише офіційні магазини і справді вигідні ціни, від −50%.\n"
    "🎁 <b>Роздачі.</b> Ігри, які зараз можна забрати безкоштовно, з датою закінчення.\n"
    "🎙 <b>Озвучка українською.</b> Що щойно вийшло в аніме й серіалах, з іменем команди й сайтом.\n"
    "📺 <b>Нові серії.</b> Популярні серіали, які вже є українською.\n\n"
    "Усе публікується автоматично кілька разів на день, без спаму. "
    "Натискайте на назву команди озвучки, щоб перейти на її канал.\n\n"
    "Дані: TMDB, AniList, CheapShark, Steam.\n"
    "This product uses the TMDB API but is not endorsed or certified by TMDB.\n"
    "@lut_i_serii"
)

CACHE_KEY = "setup:pinned-message-id"


def run_setup(ctx: Any) -> int:
    """Виставляє опис каналу та створює (або оновлює) закріплений пост. Потрібні права адміністратора бота."""
    cfg = ctx.cfg
    if cfg.dry_run:
        ctx.log(f"Опис ({len(DESCRIPTION)} символів):\n{DESCRIPTION}\n\nЗакріплений пост:\n{PINNED_TEXT}")
        return 0
    if len(DESCRIPTION) > 255:
        raise RuntimeError("Опис каналу довший за 255 символів")
    done = 0
    try:
        telegram._call(ctx.session, cfg.bot_token, "setChatDescription",
                       {"chat_id": cfg.chat_id, "description": DESCRIPTION})
        ctx.log("Опис каналу виставлено.")
        done += 1
    except telegram.TelegramError as exc:
        ctx.log(f"Опис не виставлено (потрібне право «Змінювати інформацію про канал»): {exc}")
    pinned = ctx.db.get_cache(CACHE_KEY)
    try:
        if pinned:
            try:
                telegram._call(ctx.session, cfg.bot_token, "editMessageText", {
                    "chat_id": cfg.chat_id, "message_id": int(pinned), "text": PINNED_TEXT,
                    "parse_mode": "HTML", "link_preview_options": {"is_disabled": True},
                })
                ctx.log("Закріплений пост оновлено.")
                return done + 1
            except telegram.TelegramError as exc:
                if "not modified" in str(exc).lower():
                    ctx.log("Закріплений пост уже має цей текст.")
                    return done + 1
                ctx.log(f"Оновити старий закріплений пост не вдалося, створюю новий: {exc}")
        sent = telegram._call(ctx.session, cfg.bot_token, "sendMessage", {
            "chat_id": cfg.chat_id, "text": PINNED_TEXT, "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        })
        message_id = int(sent["result"]["message_id"])
        ctx.db.set_cache(CACHE_KEY, str(message_id))
        telegram._call(ctx.session, cfg.bot_token, "pinChatMessage", {
            "chat_id": cfg.chat_id, "message_id": message_id, "disable_notification": True,
        })
        ctx.log("Закріплений пост опубліковано й закріплено.")
        done += 1
    except telegram.TelegramError as exc:
        ctx.log(f"Закріплений пост не вдався (потрібні права «Публікувати» та «Закріплювати»): {exc}")
    return done


AVATAR_PATH = "assets/avatar.png"


def run_avatar(ctx: Any) -> int:
    """Ставить assets/avatar.png аватаркою каналу (потрібне право «Змінювати інформацію про канал»)."""
    cfg = ctx.cfg
    try:
        with open(AVATAR_PATH, "rb") as fh:
            content = fh.read()
    except OSError:
        raise RuntimeError(f"Немає файлу {AVATAR_PATH}") from None
    if cfg.dry_run:
        ctx.log(f"Аватарка: {AVATAR_PATH}, {len(content)} байтів (dry-run, не надсилаю).")
        return 0
    try:
        resp = ctx.session.post(
            f"https://api.telegram.org/bot{cfg.bot_token}/setChatPhoto",
            data={"chat_id": cfg.chat_id},
            files={"photo": ("avatar.png", content, "image/png")},
            timeout=60,
        )
        data = resp.json()
    except (OSError, ValueError) as exc:  # requests.RequestException — підклас OSError; без токена в тексті
        raise RuntimeError(f"setChatPhoto: помилка мережі ({type(exc).__name__})") from None
    if not data.get("ok"):
        raise RuntimeError(f"setChatPhoto: {data.get('description', resp.status_code)}")
    ctx.log("Аватарку каналу встановлено.")
    return 1
