"""Щоденний Short: бере пости, які бот справді опублікував у каналі (журнал у базі), збирає ролик і,
якщо підключено YouTube, завантажує його.
Використання: python scripts/daily_short.py games|anime|series [--out video.mp4]"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import make_channel_short as mcs  # noqa: E402
import upload_youtube  # noqa: E402
from lutserii import published  # noqa: E402
from lutserii.config import Config  # noqa: E402
from lutserii.db import StateDB  # noqa: E402
from lutserii.posts import long_date  # noqa: E402
from lutserii.textutil import base_title  # noqa: E402

SHOWN = ROOT / "data/shorts/shown.json"
PUBLIC_FROM = "2026-11-02"  # з цієї дати (за Києвом) ролики публікуються відкрито; раніше лише приватні
WINDOW_HOURS = 30
FORGET_DAYS = 14
CHANNEL_URL = "https://t.me/lut_i_serii"
CATEGORY = {"games": "20", "free": "20", "anime": "24", "series": "24"}
HEAD = {"games": "Знижки на ігри", "free": "Безкоштовні ігри сьогодні", "anime": "Нові серії аніме сьогодні", "series": "Нові серії українською"}
KINDS_TOPIC = {"games": "deals"}  # тема ролика -> тема журналу постів (ігри для Shorts: знижки, роздачі окремо)
TAGS = {
    "games": ["ігри", "знижки", "PlayStation", "Xbox", "Steam"],
    "free": ["ігри", "безкоштовно", "роздача", "PlayStation", "Xbox", "Steam"],
    "anime": ["аніме", "нові серії", "аніме українською"],
    "series": ["серіали", "озвучка українською", "нові серії"],
}


def privacy_for(today, override: str | None = None, public_from: str | None = None) -> str:
    """public/private/unlisted: явна змінна YT_PRIVACY головніша, інакше за датою початку відкритих публікацій."""
    if override:
        return override
    start = date.fromisoformat(public_from or PUBLIC_FROM)
    return "public" if today >= start else "private"


def key_of(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:16]


SUBSCRIPTION_NAMES = (("PlayStation Plus", "PS Plus"), ("Game Pass", "Xbox Game Pass"), ("Free Play Days", "Free Play Days"))


def title_of(text: str) -> str:
    """Коротка назва гри/серіалу з першого рядка поста (без префіксів «Безкоштовно:», магазину, відсотків)."""
    first = html.unescape(re.sub(r"<[^>]+>", "", text.strip().splitlines()[0]))
    first = re.sub(r"^[^\w\"«]+", "", first)
    for marker, short in SUBSCRIPTION_NAMES:  # добірки підписок: фіксована коротка назва
        if marker in first:
            return short
    first = re.sub(r"^Серія\s+", "", first)
    first = re.sub(r"^Сьогодні вийшла:\s*", "", first)
    first = re.sub(r"^Безкоштовно( \(додатковий вміст\))?:\s*", "", first)
    first = first.split(" — ")[0].split(": знижки")[0]
    first = re.sub(r"\s*\((?:Steam|Epic Games|GOG|Xbox|PS Store|IndieGala)[^)]*\)\s*$", "", first, flags=re.I).strip()
    first = re.sub(r"[\s:\-–—]*(?:[\w']+\s+)?видання(?:\s+[\w']+)?\s*$", "", first, flags=re.I)  # «: видання Deluxe»
    first = re.sub(r"[:\-–—]\s*(?:Deluxe|Ultimate|Gold|Standard|Premium|Complete|Definitive)\s*$", "", first, flags=re.I)
    first = re.sub(r"\s+\w+\s+Edition$", "", base_title(first), flags=re.I)
    if len(first) > 38:  # обрізаємо за змістом: спершу за останнім роздільником («: », « - », «. », «(»), інакше по слову
        cuts = [m.start() for m in re.finditer(r":\s|\s-\s|\.\s|\s\(|\s—\s", first) if 12 <= m.start() <= 38]
        if cuts:
            first = first[: cuts[-1]]
        else:
            first = first[:38].rsplit(" ", 1)[0].rstrip(" ,:-") + "…"  # обрізано посеред назви: кажемо про це
    return first.rstrip(" ,:-")


def is_digest(text: str) -> bool:
    return bool(re.match(r"^\W*Озвучка українською\s*·", html.unescape(re.sub(r"<[^>]+>", "", text.strip().splitlines()[0]))))


def digest_names(text: str) -> list[str]:
    """Назви тайтлів із добірки озвучки за день: «🔹 <b>Назва</b> — 2 сезон, 5 серія» -> «Назва»."""
    return [html.unescape(n).strip() for n in re.findall(r"🔹\s*<b>(.*?)</b>", text)]


def names_of(texts: list[str]) -> list[str]:
    """Назви для заголовка й опису: з карток — назва тайтлу, з добірок озвучки — назви тайтлів зі списку."""
    out: list[str] = []
    for x in texts:
        out += digest_names(x) if is_digest(x) else [title_of(x)]
    return list(dict.fromkeys(n for n in out if n))


def merge_digests(items: list[dict]) -> list[dict]:
    """Усі добірки озвучки за добу — один кадр із повним списком (замість трьох майже однакових підряд)."""
    digests = [i for i in items if is_digest(i["text"])]
    if len(digests) < 2:
        return items
    lines: list[str] = []
    for d in digests:
        for quote in re.findall(r"<blockquote>(.*?)</blockquote>", d["text"], flags=re.S):
            for ln in quote.splitlines():
                if ln.strip().startswith("🔹") and ln.strip() not in lines:
                    lines.append(ln.strip())
    head = digests[0]["text"].strip().splitlines()[0]
    merged = {**digests[0], "text": f"{head}\n\n<blockquote>" + "\n".join(lines) + "</blockquote>",
              "photos": list(dict.fromkeys(p for d in digests for p in d["photos"]))[:6]}
    out, placed = [], False
    for i in items:
        if i in digests:
            if not placed:
                out.append(merged)
                placed = True
        else:
            out.append(i)
    return out


def make_title(topic: str, texts: list[str], day: str) -> str:
    names = names_of(texts)[:3]
    title = f"{HEAD[topic]}: {', '.join(names)}"
    return title if len(title) <= 92 else f"{HEAD[topic]}: {', '.join(names[:2])}"


def make_description(topic: str, texts: list[str], day: str) -> str:
    names = "\n".join(f"• {t}" for t in names_of(texts))
    tags = " ".join("#" + t.replace(" ", "") for t in ["Shorts", *TAGS[topic]][:6])
    return (f"{HEAD[topic]}, {day}.\n\n{names}\n\nУсі пости, знижки та роздачі щодня в Telegram: {CHANNEL_URL}\n\n{tags}")


def tiktok_caption(topic: str, texts: list[str], credit: str = "") -> str:
    """Готовий підпис для TikTok: лише текст, який лишається вставити (відео надсилається вам у приват)."""
    names = ", ".join(names_of(texts)[:6])
    tags = " ".join("#" + t.replace(" ", "") for t in [*TAGS[topic], "українською"])
    parts = [f"{HEAD[topic]}: {names}", f"Усі пости, знижки та роздачі щодня в Telegram: {CHANNEL_URL}", tags]
    if credit:
        parts.append(credit)
    return "\n\n".join(parts)[:900]


def music_credit(track: Path | None) -> str:
    """Підпис автора музики з assets/music/tracks.json (порожньо для власних треків)."""
    if track is None:
        return ""
    return mcs.load_tracks().get(track.name, {}).get("credit", "")


def load_shown() -> dict:
    try:
        return json.loads(SHOWN.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def notify(text: str) -> None:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("PREVIEW_CHAT_ID")
    if token and chat:
        try:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": text, "disable_web_page_preview": True}, timeout=30)
        except requests.RequestException:
            pass


def send_preview_video(path: Path, caption: str) -> None:
    """Надсилає зібраний ролик вам у приват (режим перегляду, поки YouTube не підключено)."""
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("PREVIEW_CHAT_ID")
    if not (token and chat):
        print("Немає TELEGRAM_BOT_TOKEN/PREVIEW_CHAT_ID: перегляд нікуди надіслати.")
        return
    with path.open("rb") as fh:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendVideo",
            data={"chat_id": chat, "caption": caption[:1000], "supports_streaming": "true", "width": 1080, "height": 1920},
            files={"video": (path.name, fh, "video/mp4")}, timeout=300,
        )
    if resp.status_code != 200:
        print(f"Перегляд не надіслано: {resp.status_code} {resp.text[:200]}")


def distinct_photos(items: list[dict]) -> list[str | None]:
    """Картинка кожного поста: перша, якої ще не було раніше; якщо всі вже були, лишається власна перша
    (пост без картинки не робимо, він має бути гарним)."""
    used: set[str] = set()
    out: list[str | None] = []
    for item in items:
        photos = item["photos"]
        pick = next((p for p in photos if p not in used), photos[0] if photos else None)
        if pick:
            used.add(pick)
        out.append(pick)
    return out


def arrange(items: list[dict]) -> list[tuple[dict, str | None]]:
    """Порядок постів у ролику: з тією самою картинкою не йдуть поспіль (картка серіалу й добірка з тією ж обкладинкою).
    Решта порядку лишається, як було."""
    pairs = list(zip(items, distinct_photos(items)))
    out: list[tuple[dict, str | None]] = []
    while pairs:
        prev = out[-1][1] if out else None
        index = next((n for n, (_, ph) in enumerate(pairs) if not ph or ph != prev), 0)
        out.append(pairs.pop(index))
    return out


def run_topic(topic: str, out: Path, retro: bool, preview: bool, test: bool = False, redo: bool = False) -> None:
    """Збирає ролик теми з постів каналу; preview=True: надсилає вам у приват і нічого не позначає показаним.
    test=True: справжнє завантаження на YouTube, але приватне, без TikTok і без позначки «показано» (щоб подивитися, як ролик виглядає там)."""
    cfg = Config.from_env()
    now = int(time.time())
    db = StateDB(cfg.db_path)
    try:
        items = published.recent(db, KINDS_TOPIC.get(topic, topic), now - WINDOW_HOURS * 3600)
    finally:
        db.close()
    shown = load_shown()
    seen = {k for k, ts in shown.get(topic, {}).items() if ts > now - FORGET_DAYS * 86400}
    if topic == "free":  # роздачі, які вже були в минулих «ігрових» роликах (до розділення), не повторюємо
        seen |= {k for k, ts in shown.get("games", {}).items() if ts > now - FORGET_DAYS * 86400}
    if redo:  # перезбірка сьогоднішнього в новому форматі: пости, показані СЬОГОДНІ, знову доступні (старіші лишаються показаними)
        midnight = int(datetime.now(ZoneInfo(cfg.timezone)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        seen = {k for k, ts in shown.get(topic, {}).items() if ts <= midnight}
        if topic == "free":
            seen |= {k for k, ts in shown.get("games", {}).items() if ts <= midnight}
    if preview or test:  # перегляд нічого не позначає показаним, тож і вже показане не відсіюється: інакше після публікації його не побачити
        seen = set()
    unseen = [i for i in items if key_of(i["text"]) not in seen]
    slides = merge_digests(unseen)[: mcs.MAX_POSTS]  # добірки озвучки за добу зливаються в один кадр
    if len(slides) < 2:
        print(f"Тема «{topic}»: свіжих кадрів {len(slides)}, ролик пропущено.")
        if preview:
            notify(f"🎞 Перегляд Shorts: тема «{topic}», за останню добу замало нових постів ({len(slides)}), ролик не збирався.")
        return
    arranged = arrange(slides)
    with_digest = any(is_digest(i["text"]) for i in slides)  # усі злиті добірки вважаються показаними разом із кадром
    fresh = [i for i in unseen if (i in slides and not is_digest(i["text"])) or (with_digest and is_digest(i["text"]))]
    messages = [(i["text"], mcs.load_art([p, *[x for x in i["photos"] if x != p]]) if p else None) for i, p in arranged]
    recent_tracks = list(shown.get("_music_recent", []))[-6:]  # трек не повторюється, поки не пройдуть кілька відео поспіль
    chosen = None if retro else mcs.choose_track(topic, recent_tracks or shown.get("_music", {}).get(topic))
    starts = shown.get("_music_starts", {})
    used = mcs.render(messages, topic, out, str(chosen) if chosen else None, retro=retro,
                      music_avoid=starts.get(chosen.name, []) if chosen else ())
    track = used[0] if used else None
    day = long_date(datetime.now(ZoneInfo(cfg.timezone)).date())
    texts = [i["text"] for i in fresh]
    title, description = make_title(topic, texts, day), make_description(topic, texts, day)
    credit = music_credit(track)
    if credit:  # атрибуція автора музики (вимога ліцензії, напр. Uppbeat)
        description += f"\n\n{credit}"
    print(f"Назва: {title}")
    if preview:
        send_preview_video(out, f"🎞 Перегляд ({topic}): так виглядатиме Short\n\n{title}\n\n{credit}\n\nПерегляд: на YouTube нічого не завантажено, пости не позначено показаними.")
        print("Перегляд надіслано в приват, пости не позначено показаними.")
        return
    privacy = "private" if test else privacy_for(datetime.now(ZoneInfo(cfg.timezone)).date(), os.environ.get("YT_PRIVACY"), os.environ.get("YT_PUBLIC_FROM"))
    video_id = upload_youtube.upload(out, title, description, ["Shorts", *TAGS[topic]], privacy, CATEGORY[topic])
    notify(f"▶️ Short опубліковано ({privacy}): https://youtube.com/shorts/{video_id}\n{title}")
    print(f"Завантажено: {video_id} ({privacy})")
    if test:
        print("Тестове завантаження: без TikTok, пости не позначено показаними.")
        return
    try:  # те саме відео з готовим підписом для TikTok: лишається натиснути «Поділитися» → TikTok
        send_preview_video(out, tiktok_caption(topic, texts, credit))
    except Exception as exc:  # відправка в приват не повинна ламати публікацію
        print(f"Для TikTok не надіслано: {type(exc).__name__}: {exc}")
    if track is not None:
        shown.setdefault("_music", {})[topic] = track.name
        shown["_music_recent"] = ([*recent_tracks, track.name])[-6:]
        shown.setdefault("_music_starts", {})[track.name] = [*starts.get(track.name, []), used[1]][-4:]
    shown.setdefault(topic, {}).update({key_of(t): now for t in texts})
    shown[topic] = {k: ts for k, ts in shown[topic].items() if ts > now - FORGET_DAYS * 86400}
    SHOWN.parent.mkdir(parents=True, exist_ok=True)
    SHOWN.write_text(json.dumps(shown, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("topic", choices=[*mcs.TOPICS, "all"])
    parser.add_argument("--out", default="out/short.mp4")
    parser.add_argument("--retro", action="store_true")
    parser.add_argument("--preview", action="store_true", help="надіслати ролик вам у приват замість YouTube")
    parser.add_argument("--redo", action="store_true", help="перезібрати сьогоднішні ролики: пости, показані сьогодні, знову беруться в роботу")
    parser.add_argument("--test", action="store_true", help="тестове завантаження на YouTube: приватне, без TikTok, пости не позначаються показаними")
    args = parser.parse_args()
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    live = upload_youtube.configured()
    preview = args.preview or not live
    topics = list(mcs.TOPICS) if args.topic == "all" else [args.topic]
    if preview and event == "schedule":
        # Тестовий режим за розкладом: один запуск на день (вечірній) надсилає всі три ролики
        if args.topic != "games":
            print("YouTube не підключено: за розкладом перегляд надсилається одним вечірнім запуском.")
            return 0
        topics = list(mcs.TOPICS)
    for topic in topics:
        try:
            run_topic(topic, Path(args.out).with_name(f"{topic}.mp4"), args.retro, preview, args.test and not preview, args.redo)
        except Exception as exc:  # одна тема не повинна ламати решту
            print(f"{topic}: {type(exc).__name__}: {exc}")
            notify(f"⚠️ Short «{topic}» не вдалося зібрати: {type(exc).__name__}: {exc}"[:500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
