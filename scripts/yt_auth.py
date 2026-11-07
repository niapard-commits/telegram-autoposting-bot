"""Одноразова авторизація YouTube без комп'ютера: показує код, який треба ввести на google.com/device
(повідомлення приходить вам у Telegram від бота), і надсилає назад refresh token.
Потрібні змінні: YT_CLIENT_ID, YT_CLIENT_SECRET, TELEGRAM_BOT_TOKEN, PREVIEW_CHAT_ID."""

from __future__ import annotations

import os
import sys
import time

import requests

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube"]


def tell(text: str) -> None:
    requests.post(
        f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage",
        json={"chat_id": os.environ["PREVIEW_CHAT_ID"], "text": text, "disable_web_page_preview": True}, timeout=30,
    )


def main() -> int:
    cid, secret = os.environ["YT_CLIENT_ID"], os.environ["YT_CLIENT_SECRET"]
    for scope in SCOPES:  # пробуємо вужчий дозвіл, якщо Google не приймає, то ширший
        r = requests.post("https://oauth2.googleapis.com/device/code", data={"client_id": cid, "scope": scope}, timeout=30)
        if r.status_code == 200:
            break
        print(f"Дозвіл {scope}: {r.status_code} {r.text[:200]}", file=sys.stderr)
    else:
        tell(f"YouTube: Google не видав код ({r.status_code}): {r.text[:300]}")
        return 1
    d = r.json()
    tell(f"▶️ Підключення YouTube\n\n1) Відкрийте {d['verification_url']}\n2) Введіть код: {d['user_code']}\n"
         f"3) Увійдіть в акаунт каналу й дайте доступ.\n\nКод діє {d['expires_in'] // 60} хв.")
    deadline = time.time() + d["expires_in"]
    while time.time() < deadline:
        time.sleep(d.get("interval", 5))
        t = requests.post("https://oauth2.googleapis.com/token", data={
            "client_id": cid, "client_secret": secret, "device_code": d["device_code"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code"}, timeout=30)
        body = t.json()
        if t.status_code == 200:
            tell("✅ YouTube підключено. Refresh token наступним повідомленням: додайте його в GitHub як секрет "
                 "YT_REFRESH_TOKEN, а повідомлення з токеном видаліть.")
            tell(body["refresh_token"])
            return 0
        if body.get("error") not in ("authorization_pending", "slow_down"):
            tell(f"YouTube: помилка підключення: {body.get('error')} {body.get('error_description', '')}")
            return 1
    tell("YouTube: час на введення коду вийшов, запустіть підключення ще раз.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
