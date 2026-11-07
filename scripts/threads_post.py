"""Публікація в Threads: чергує пости-гачки (із ротацією варіантів) і короткі пости зі свіжих новин каналу.
Без токена працює як перегляд: текст приходить вам у приват (Telegram).
Використання: python scripts/threads_post.py games|anime|series [--preview] [--kind auto|hook|data]"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from lutserii import published  # noqa: E402
from lutserii.config import Config  # noqa: E402
from lutserii.db import StateDB  # noqa: E402

API = "https://graph.threads.net"
HOOKS = ROOT / "data/threads_hooks.json"
STATE = ROOT / "data/threads/state.json"
TOKEN_FILE = ROOT / "data/threads/token.json"
LINK = "t.me/lut_i_serii"
LIVE_FROM = "2026-11-02"  # з цієї дати (за Києвом) пости справді йдуть у Threads; раніше лише перегляд вам у приват
LIMIT = 480  # справжній ліміт Threads 500 символів, лишаємо запас
WINDOW_HOURS = 30
FORGET_DAYS = 14
TAG = {"games": "#ігри", "anime": "#аніме", "series": "#серіали"}
POOL = {"games": "games", "anime": "anime", "series": "series"}  # окремі набори гачків: щоб аніме й серіали не повторювались
INTROS = {
    "games": ["🎮 Що цікавого сьогодні по іграх:", "🎮 Свіжі знижки та роздачі від каналу:", "🎮 Не пропусти сьогодні:"],
    "anime": ["📺 Сьогодні в каналі по аніме:", "📺 Нове по аніме, поки ти не пропустив:", "📺 Дивимось сьогодні:"],
    "series": ["🎬 Українською з'явилось:", "🎬 Нове з озвучкою, без спойлерів:", "🎬 Свіже з українською озвучкою:"],
}
OUTROS = {
    "games": ["Що береш? Пиши в коментарях 👇", "З ким граєш на вихідних? 👇", "Яка гра у вас у списку бажань? 👇"],
    "anime": ["Яку серію чекаєш найбільше? 🔥", "Що дивишся цього тижня? 👇", "Розкажи, яке аніме в тебе зараз №1 👇"],
    "series": ["Що вже встиг подивитись? 👇", "Яку озвучку любиш найбільше? 👇", "Який серіал чекаєш наступним? 🔥"],
}


# ---------- стан: ротація варіантів, що вже показували ----------

def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def pick_variant(pool: str, hooks: list[str], state: dict, now: int) -> tuple[int, str]:
    """Найрідше показаний варіант; при рівності той, що показували найдавніше."""
    used = state.setdefault("uses", {}).setdefault(pool, {})
    last = state.setdefault("last_used", {}).setdefault(pool, {})
    idx = min(range(len(hooks)), key=lambda i: (used.get(str(i), 0), last.get(str(i), 0), i))
    used[str(idx)] = used.get(str(idx), 0) + 1
    last[str(idx)] = now
    return idx, hooks[idx]


def refresh_reminder(pool: str, hooks: list[str], state: dict) -> str | None:
    """Коли всі варіанти показано вже по колу кілька разів, нагадуємо додати нові (один раз на кожне коло)."""
    used = state.setdefault("uses", {}).get(pool, {})
    rounds = min((used.get(str(i), 0) for i in range(len(hooks))), default=0)
    notified = state.setdefault("notified", {})
    if rounds >= 2 and notified.get(pool, 0) < rounds:
        notified[pool] = rounds
        return (f"🧵 Threads: усі {len(hooks)} варіантів гачків «{pool}» уже показано по {rounds} разів. "
                "Надішліть мені нові ідеї або напишіть «онови тексти Threads», щоб не повторюватись.")
    return None


# ---------- тексти ----------

def discount_of(text: str) -> int | None:
    m = re.search(r"[−-]\s?(\d{1,2})%", text) or re.search(r"знижк\w*\s+(?:до\s+)?(\d{1,2})%", text)
    return int(m.group(1)) if m else None


def compose_hook(topic: str, hook: str) -> str:
    return f"{hook}\n\n{LINK}\n\n{TAG[topic]}"[:500]


def compose_data(topic: str, items: list[dict], state: dict) -> str | None:
    """Пост зі свіжих новин каналу: до 3 назв (і знижка, якщо є). None, якщо матеріалу замало."""
    import daily_short as ds
    rows, seen = [], set()
    for it in items:
        name = ds.title_of(it["text"])
        if re.search(r"[А-Яа-яІіЇїЄєҐґ]", name):  # «Чорна Конюшина (Black Clover Season 2)» -> українська назва без дужок
            name = re.sub(r"\s*\([A-Za-z][^)]*\)\s*$", "", name).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        pct = discount_of(it["text"])
        rows.append(f"• {name}" + (f": −{pct}%" if pct else ""))
    if len(rows) < 2:
        return None
    step = state.setdefault("variant", {})
    i = step.get(topic, 0)
    step[topic] = i + 1
    intro, outro = INTROS[topic][i % len(INTROS[topic])], OUTROS[topic][i % len(OUTROS[topic])]
    for n in range(min(len(rows), 3), 1, -1):  # скорочуємо список, доки не влізе
        text = f"{intro}\n\n" + "\n".join(rows[:n]) + f"\n\n{outro}\nУсе в каналі: {LINK}\n\n{TAG[topic]}"
        if len(text) <= LIMIT:
            return text
    return None


# ---------- Threads API ----------

def load_token() -> dict | None:
    if TOKEN_FILE.exists():
        try:
            data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
            if data.get("access_token") and data.get("user_id"):
                return data
        except (OSError, ValueError):
            pass
    if os.environ.get("THREADS_ACCESS_TOKEN") and os.environ.get("THREADS_USER_ID"):
        return {"access_token": os.environ["THREADS_ACCESS_TOKEN"], "user_id": os.environ["THREADS_USER_ID"], "expires_at": 0}
    return None


def refresh_if_needed(tok: dict, now: int) -> dict:
    """Довгий токен живе 60 днів; оновлюємо, коли лишається менше 30 (оновлення дозволене не раніше доби після видачі)."""
    if not tok.get("expires_at") or tok["expires_at"] - now > 30 * 86400:
        return tok
    r = requests.get(f"{API}/refresh_access_token", params={
        "grant_type": "th_refresh_token", "access_token": tok["access_token"]}, timeout=30)
    if r.status_code != 200:
        print(f"Threads: токен не оновлено ({r.status_code}): {r.text[:200]}", file=sys.stderr)
        return tok
    body = r.json()
    tok = {**tok, "access_token": body["access_token"], "expires_at": now + int(body.get("expires_in", 60 * 86400))}
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(tok, indent=1), encoding="utf-8")
    return tok


def jpeg_url(url: str) -> str:
    """Threads бере лише JPEG і PNG; webp (як у PSprices) проганяємо через публічний конвертер wsrv.nl."""
    path = url.split("?")[0].lower()
    if path.endswith((".jpg", ".jpeg", ".png")):
        return url
    return f"https://wsrv.nl/?url={quote(url, safe='')}&output=jpg&q=90"


def publish(tok: dict, text: str, image_url: str | None = None) -> str:
    """Створює пост у Threads. Повертає id. Картинка, яку Threads не взяв, не заважає: публікуємо без неї."""
    uid, access = tok["user_id"], tok["access_token"]

    def container(with_image: bool) -> str:
        data = {"text": text, "access_token": access}
        data.update({"media_type": "IMAGE", "image_url": jpeg_url(image_url)} if with_image else {"media_type": "TEXT"})
        r = requests.post(f"{API}/v1.0/{uid}/threads", data=data, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"Threads: контейнер ({r.status_code}): {r.text[:300]}")
        return r.json()["id"]

    try:
        cid = container(bool(image_url))
    except RuntimeError as exc:
        if not image_url:
            raise
        print(f"{exc}; пробуємо без картинки", file=sys.stderr)
        cid = container(False)
    last = ""
    for attempt in range(5):  # Threads обробляє контейнер кілька секунд
        time.sleep(5 if attempt == 0 else 8)
        r = requests.post(f"{API}/v1.0/{uid}/threads_publish", data={"creation_id": cid, "access_token": access}, timeout=60)
        if r.status_code == 200:
            return r.json()["id"]
        last = f"{r.status_code}: {r.text[:300]}"
    raise RuntimeError(f"Threads: публікація не вдалася ({last})")


def notify(text: str) -> None:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("PREVIEW_CHAT_ID")
    if token and chat:
        try:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": text, "disable_web_page_preview": True}, timeout=30)
        except requests.RequestException:
            pass


# ---------- запуск ----------

def go_live(today, live_from: str | None = None) -> bool:
    """Чи вже почалися справжні публікації (THREADS_LIVE_FROM головніша за стандартну дату)."""
    from datetime import date
    return today >= date.fromisoformat(live_from or os.environ.get("THREADS_LIVE_FROM") or LIVE_FROM)


def run(topic: str, kind: str, preview: bool) -> None:
    import daily_short as ds
    from zoneinfo import ZoneInfo
    cfg = Config.from_env()
    now = int(time.time())
    waiting = not go_live(datetime.now(ZoneInfo(cfg.timezone)).date())
    preview = preview or waiting  # до дати старту навіть за розкладом лише надсилаємо перегляд в приват
    state = load_state()
    hooks = json.loads(HOOKS.read_text(encoding="utf-8"))[POOL[topic]]
    shown = {k: ts for k, ts in state.setdefault("shown", {}).get(topic, {}).items() if ts > now - FORGET_DAYS * 86400}

    db = StateDB(cfg.db_path)
    try:
        items = [i for i in published.recent(db, topic, now - WINDOW_HOURS * 3600)
                 if i["photos"] and ds.key_of(i["text"]) not in shown]
    finally:
        db.close()

    if kind == "all":  # лише перегляд: усі варіанти гачків і пост з новин, стан ротації не змінюємо
        for n, hook in enumerate(hooks, 1):
            notify(f"🧵 Threads · {topic} · гачок {n}/{len(hooks)}:\n\n{compose_hook(topic, hook)}")
        sample = compose_data(topic, items, state)
        notify(f"🧵 Threads · {topic} · пост з новин:\n\n{sample}" + (f"\n\n(картинка: {items[0]['photos'][0]})" if sample else "")
               if sample else f"🧵 Threads · {topic} · поста з новин зараз нема: замало свіжих матеріалів.")
        print(f"Перегляд усіх варіантів ({topic}) надіслано в приват, нічого не опубліковано.")
        return

    last_kind = state.setdefault("last_kind", {}).get(topic)
    want_data = kind == "data" or (kind == "auto" and last_kind != "data")  # гачки та новини чергуються
    text, image, used_items = None, None, []
    if want_data:
        text = compose_data(topic, items, state)
        if text:
            used_items, image = items, items[0]["photos"][0]
    if text is None:
        if kind == "data":
            print("Для поста з новин замало свіжих матеріалів, пропускаю.")
            return
        _, hook = pick_variant(POOL[topic], hooks, state, now)
        text, image, this_kind = compose_hook(topic, hook), None, "hook"
    else:
        this_kind = "data"

    tok = None if preview else load_token()
    if tok is None:
        notify(f"🧵 Перегляд Threads ({topic}, {'новини' if this_kind == 'data' else 'гачок'}):\n\n{text}"
               + (f"\n\n(картинка: {image})" if image else "")
               + (f"\n\nПерегляд: у Threads нічого не опубліковано (справжні пости з {os.environ.get('THREADS_LIVE_FROM') or LIVE_FROM})."
                  if waiting else "\n\nThreads ще не підключено, нічого не опубліковано."))
        print("Перегляд надіслано в приват, нічого не опубліковано.")
    else:
        tok = refresh_if_needed(tok, now)
        post_id = publish(tok, text, image)
        print(f"Опубліковано в Threads: {post_id} ({this_kind})")
        notify(f"🧵 Threads опубліковано ({topic}, {this_kind}): {text.splitlines()[0][:80]}")
        shown.update({ds.key_of(i["text"]): now for i in used_items[:3]})
    state["shown"][topic] = shown
    state["last_kind"][topic] = this_kind
    reminder = refresh_reminder(POOL[topic], hooks, state)
    if reminder:
        notify(reminder)
    save_state(state)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("topic", choices=list(TAG))  # games | anime | series
    parser.add_argument("--kind", choices=["auto", "hook", "data", "all"], default="auto")
    parser.add_argument("--preview", action="store_true", help="надіслати текст у приват замість публікації")
    args = parser.parse_args()
    try:
        run(args.topic, args.kind, args.preview)
    except Exception as exc:  # збій одного поста не повинен валити весь запуск
        print(f"threads {args.topic}: {type(exc).__name__}: {exc}")
        notify(f"⚠️ Threads «{args.topic}»: {type(exc).__name__}: {exc}"[:500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
