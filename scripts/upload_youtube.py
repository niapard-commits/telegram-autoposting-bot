"""Завантаження відео на YouTube через Data API (resumable upload) за refresh token.
Потрібні змінні: YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN."""

from __future__ import annotations

import json
import os
from pathlib import Path

import requests

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"


def configured() -> bool:
    return all(os.environ.get(k) for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"))


def access_token() -> str:
    resp = requests.post(TOKEN_URL, data={
        "client_id": os.environ["YT_CLIENT_ID"], "client_secret": os.environ["YT_CLIENT_SECRET"],
        "refresh_token": os.environ["YT_REFRESH_TOKEN"], "grant_type": "refresh_token",
    }, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"YouTube: не вдалося оновити доступ ({resp.status_code}): {resp.text[:300]}")
    return resp.json()["access_token"]


def upload(path: Path, title: str, description: str, tags: list[str], privacy: str = "private", category: str = "24") -> str:
    """Повертає id завантаженого відео."""
    token = access_token()
    meta = {
        "snippet": {"title": title[:100], "description": description[:4900], "tags": tags[:15],
                    "categoryId": category, "defaultLanguage": "uk", "defaultAudioLanguage": "uk"},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False, "embeddable": True},
    }
    size = path.stat().st_size
    start = requests.post(
        UPLOAD_URL, params={"uploadType": "resumable", "part": "snippet,status"},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)},
        data=json.dumps(meta), timeout=60,
    )
    if start.status_code != 200:
        raise RuntimeError(f"YouTube: не вдалося почати завантаження ({start.status_code}): {start.text[:400]}")
    done = requests.put(
        start.headers["Location"], data=path.read_bytes(),
        headers={"Content-Type": "video/mp4", "Content-Length": str(size)}, timeout=600,
    )
    if done.status_code not in (200, 201):
        raise RuntimeError(f"YouTube: завантаження не вдалося ({done.status_code}): {done.text[:400]}")
    return done.json()["id"]
