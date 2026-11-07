"""Одноразове підключення Threads: обмінює короткий токен (з Meta for Developers) на довгий (60 днів) і запам'ятовує id акаунта.
Потрібні змінні: THREADS_APP_SECRET, THREADS_SHORT_TOKEN, TELEGRAM_BOT_TOKEN, PREVIEW_CHAT_ID."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import threads_post as tp  # noqa: E402


FALLBACK_TTL = 55 * 86400  # довгий токен із генератора Meta діє ~60 днів; беремо з запасом


def main() -> int:
    # .strip(): зайвий пробіл чи перенос рядка при вставленні в секрет GitHub дає «Invalid Client Secret».
    secret = os.environ.get("THREADS_APP_SECRET", "").strip()
    token = os.environ["THREADS_SHORT_TOKEN"].strip()
    access_token, ttl, exchange_error = token, FALLBACK_TTL, None

    if secret:
        r = requests.get(f"{tp.API}/access_token", params={
            "grant_type": "th_exchange_token", "client_secret": secret, "access_token": token}, timeout=30)
        if r.status_code == 200:
            body = r.json()
            access_token, ttl = body["access_token"], int(body.get("expires_in", 60 * 86400))
        else:
            exchange_error = f"{r.status_code}: {r.text[:200]}"
    else:
        exchange_error = "THREADS_APP_SECRET не задано"

    # Генератор токенів у Meta видає вже довгостроковий токен, тож обмін може бути зайвим або не вдатися.
    # Тоді перевіряємо токен напряму і зберігаємо його як є.
    me = requests.get(f"{tp.API}/v1.0/me", params={"fields": "id,username", "access_token": access_token}, timeout=30)
    if me.status_code != 200:
        extra = f" Обмін на довгий токен: {exchange_error}." if exchange_error else ""
        tp.notify(f"Threads: не вдалося дізнатись акаунт ({me.status_code}): {me.text[:300]}.{extra}")
        return 1
    who = me.json()
    tp.TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    tp.TOKEN_FILE.write_text(json.dumps({
        "access_token": access_token, "user_id": who["id"],
        "expires_at": int(time.time()) + ttl}, indent=1), encoding="utf-8")
    msg = (f"✅ Threads підключено: @{who.get('username', who['id'])}. Токен збережено, бот сам його оновлюватиме. "
           "Секрети THREADS_SHORT_TOKEN і THREADS_APP_SECRET після цього можна видалити.")
    if exchange_error:
        msg += (f"\nОбмін на довгий токен не потрібен або не вдався ({exchange_error}), тому токен збережено як є "
                "(розраховано на ~55 днів). Якщо це був короткий токен на 1 годину, він скоро перестане працювати: "
                "згенеруйте токен у Meta заново.")
    tp.notify(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
