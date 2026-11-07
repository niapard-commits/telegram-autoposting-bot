"""Відправка постів у Telegram через Bot API."""

from __future__ import annotations

import html
import json
import re
from urllib.parse import quote
from typing import Any

import requests

TIMEOUT = 20
SHARE: dict | None = None  # кнопка «Поділитись» під постами (задає build_context); альбоми кнопок не підтримують


def share_markup(channel: str, text: str = "Знижки на ігри, нові серії аніме й серіалів українською") -> dict | None:
    """Кнопка, що відкриває в Telegram вікно «переслати другу» з посиланням на канал. channel: «@username» або порожньо."""
    if not channel.startswith("@"):
        return None
    link = f"https://t.me/{channel[1:]}"
    url = f"https://t.me/share/url?url={quote(link, safe='')}&text={quote(text, safe='')}"
    return {"inline_keyboard": [[{"text": "📤 Поділитись з другом", "url": url}]]}


def _markup(payload: dict) -> dict:
    return {**payload, "reply_markup": SHARE} if SHARE else payload
CAPTION_LIMIT = 1024  # ліміт підпису до фото в Telegram


def visible_len(markup: str) -> int:
    """Довжина тексту так, як її рахує Telegram: без тегів, зі сутностями (&amp; тощо) як одним символом."""
    return len(html.unescape(re.sub(r"<[^>]+>", "", markup)))


class TelegramError(RuntimeError):
    pass


def _call(session: Any, token: str, method: str, payload: dict) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        resp = session.post(url, json=payload, timeout=TIMEOUT)
    except requests.RequestException as exc:
        # Не передаємо exc далі: у повідомленні може бути URL із токеном.
        raise TelegramError(f"{method}: помилка мережі ({type(exc).__name__})") from None
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if not data.get("ok"):
        raise TelegramError(f"{method}: {data.get('description', resp.status_code)}")
    return data


MAX_PHOTO_BYTES = 9 * 1024 * 1024  # ліміт Bot API на фото, яке ми завантажуємо самі — 10 МБ
MIN_PHOTO_BYTES = 1024


def _upload_photo(session: Any, token: str, chat_id: str, text: str, url: str) -> None:
    """Telegram не зміг сам узяти картинку за посиланням (захист сайту, дивний формат): завантажуємо її самі."""
    resp = session.get(url, timeout=TIMEOUT)
    content = resp.content
    kind = str(getattr(resp, "headers", {}).get("Content-Type", "image/jpeg")).split(";")[0].strip().lower()
    if resp.status_code != 200 or not kind.startswith("image/") or not (MIN_PHOTO_BYTES <= len(content) <= MAX_PHOTO_BYTES):
        raise TelegramError("картинка недоступна або не підходить")
    try:
        result = session.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data={"chat_id": chat_id, "caption": text, "parse_mode": "HTML",
                  **({"reply_markup": json.dumps(SHARE)} if SHARE else {})},
            files={"photo": ("cover.jpg", content, kind)},
            timeout=60,
        )
        data = result.json()
    except (requests.RequestException, ValueError):
        raise TelegramError("sendPhoto (файл): помилка") from None
    if not data.get("ok"):
        raise TelegramError(f"sendPhoto (файл): {data.get('description', result.status_code)}")


def _send_photo(session: Any, token: str, chat_id: str, text: str, photo: str) -> bool:
    try:
        _call(session, token, "sendPhoto", _markup({"chat_id": chat_id, "photo": photo, "caption": text, "parse_mode": "HTML"}))
        return True
    except TelegramError as exc:
        print(f"sendPhoto за посиланням не вдалося: {exc}")
    try:
        _upload_photo(session, token, chat_id, text, photo)
        return True
    except Exception as exc:  # запасний шлях не має ламати публікацію
        print(f"Завантаження картинки не вдалося: {type(exc).__name__}: {exc}")
        return False


def send_message(
    session: Any, token: str, chat_id: str, text: str, photo: str | list[str] | None = None
) -> None:
    """Шле пост із картинкою (за потреби пробує запасні); якщо жодна не пройшла чи підпис задовгий, шле текст."""
    candidates = [photo] if isinstance(photo, str) else list(photo or [])
    if candidates and visible_len(text) <= CAPTION_LIMIT:
        for candidate in candidates[:4]:
            if _send_photo(session, token, chat_id, text, candidate):
                return
        print("Жодна картинка не пройшла, шлемо текстом.")
    _call(
        session,
        token,
        "sendMessage",
        _markup({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }),
    )


def send_album(session: Any, token: str, chat_id: str, text: str, photos: list[str]) -> None:
    media = [{"type": "photo", "media": url} for url in photos[:10]]
    media[0]["caption"] = text
    media[0]["parse_mode"] = "HTML"
    _call(session, token, "sendMediaGroup", {"chat_id": chat_id, "media": media})


def send_post(
    session: Any, token: str, chat_id: str, text: str, photos: list[str] | None = None
) -> None:
    """Альбом (2+ картинки), одне фото або текст; при збої м'яко спрощуємо."""
    photos = photos or []
    if len(photos) >= 2 and visible_len(text) <= CAPTION_LIMIT:
        try:
            send_album(session, token, chat_id, text, photos)
            return
        except TelegramError as exc:
            print(f"sendMediaGroup не вдалося, шлемо з однією картинкою: {exc}")
    send_message(session, token, chat_id, text, photos)


MAX_VIDEO_BYTES = 49 * 1024 * 1024  # ліміт Bot API на файл, який ми завантажуємо самі


def _upload_video(
    session: Any, token: str, chat_id: str, text: str, content: bytes
) -> None:
    try:
        resp = session.post(
            f"https://api.telegram.org/bot{token}/sendVideo",
            data={
                "chat_id": chat_id, "caption": text, "parse_mode": "HTML",
                "supports_streaming": "true",
                **({"reply_markup": json.dumps(SHARE)} if SHARE else {}),
            },
            files={"video": ("trailer.mp4", content, "video/mp4")},
            timeout=180,
        )
    except requests.RequestException as exc:
        raise TelegramError(f"sendVideo: помилка мережі ({type(exc).__name__})") from None
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if not data.get("ok"):
        raise TelegramError(f"sendVideo: {data.get('description', resp.status_code)}")


def send_video_post(
    session: Any, token: str, chat_id: str, text: str, video: str, photos: list[str] | None = None
) -> None:
    """Трейлер з підписом. Спершу Telegram сам бере файл за посиланням; якщо не вийшло, завантажуємо
    його самі (до 49 МБ); якщо й так ні, шлемо звичайний альбом зі скріншотів."""
    if video and visible_len(text) <= CAPTION_LIMIT:
        try:
            _call(
                session, token, "sendVideo",
                _markup({
                    "chat_id": chat_id, "video": video, "caption": text, "parse_mode": "HTML",
                    "supports_streaming": True,
                }),
            )
            return
        except TelegramError as exc:
            print(f"sendVideo за посиланням не вдалося, завантажую файл: {exc}")
        try:
            resp = session.get(video, timeout=120)
            resp.raise_for_status()
            content = resp.content
            if not content or len(content) > MAX_VIDEO_BYTES:
                raise TelegramError("файл трейлера завеликий або порожній")
            _upload_video(session, token, chat_id, text, content)
            return
        except (TelegramError, requests.RequestException) as exc:
            print(f"Трейлер не надіслано, шлемо картинки: {type(exc).__name__}")
    send_post(session, token, chat_id, text, photos)
