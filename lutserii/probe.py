"""Пробні запити до IsThereAnyDeal: зберігає справжні відповіді API у data/probe/, щоб підлаштувати розбір."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

BASE = "https://api.isthereanydeal.com"
TIMEOUT = 20
OUT = "data/probe/itad.json"


def shrink(obj: Any, depth: int = 0) -> Any:
    """Скорочує відповідь: до 3 елементів списку й 300 символів у рядках."""
    if isinstance(obj, list):
        return [shrink(x, depth + 1) for x in obj[:3]]
    if isinstance(obj, dict):
        return {k: shrink(v, depth + 1) for k, v in list(obj.items())[:40]}
    if isinstance(obj, str):
        return obj[:300]
    return obj


def find_game_id(body: Any) -> str | None:
    if isinstance(body, dict):
        game = body.get("game")
        if isinstance(game, dict) and isinstance(game.get("id"), str):
            return game["id"]
        if isinstance(body.get("id"), str):
            return body["id"]
    return None


def call(session: Any, key: str, method: str, path: str, params: dict | None = None, body: Any = None) -> dict:
    """Один запит; у результат ключ не потрапляє ні в якому вигляді."""
    all_params = {"key": key, **(params or {})}
    try:
        if method == "GET":
            resp = session.get(BASE + path, params=all_params, timeout=TIMEOUT)
        else:
            resp = session.post(BASE + path, params=all_params, json=body, timeout=TIMEOUT)
    except Exception as exc:
        return {"request": f"{method} {path}", "params": params or {}, "error": type(exc).__name__}
    raw: Any = None
    try:
        raw = resp.json()
        payload: Any = shrink(raw)
    except ValueError:
        payload = (getattr(resp, "text", "") or "")[:1000]
    return {
        "request": f"{method} {path}",
        "params": params or {},
        "body_sent": body,
        "status": resp.status_code,
        "response": payload,
        "_raw": raw,  # лише для наступних запитів; у файл не потрапляє
    }


def run_probe(session: Any, key: str, out_path: str = OUT) -> list[dict]:
    results: list[dict] = []
    if not key:
        results.append({"error": "ITAD_API_KEY не задано"})
    else:
        lookups = [
            call(session, key, "GET", "/games/lookup/v1", {"title": "Portal 2"}),
            call(session, key, "GET", "/games/lookup/v1", {"appid": "620"}),
            call(session, key, "GET", "/games/lookup/v1", {"appid": "app/620"}),
        ]
        results += lookups
        game_id = next((i for i in (find_game_id(r.get("response")) for r in lookups) if i), None)
        if game_id:
            results += [
                call(session, key, "POST", "/games/prices/v3", {"country": "UA", "deals": "true"}, [game_id]),
                call(session, key, "POST", "/games/historylow/v1", {"country": "UA"}, [game_id]),
                call(session, key, "GET", "/games/info/v2", {"id": game_id}),
            ]
        deals = call(session, key, "GET", "/deals/v2", {"country": "UA", "limit": "3"})
        results.append(deals)
        raw = deals.get("_raw")
        items = raw.get("list") if isinstance(raw, dict) else None
        live_id = items[0].get("id") if items and isinstance(items[0], dict) else None
        if live_id:  # гра, яка зараз на знижці: так видно справжню форму відповіді про ціни
            results += [
                call(session, key, "POST", "/games/prices/v3", {"country": "UA"}, [live_id]),
                call(session, key, "POST", "/games/prices/v3", {"country": "UA", "shops": "61"}, [live_id]),
            ]
        if game_id:
            results.append(
                call(session, key, "POST", "/games/prices/v3", {"country": "UA", "nondeals": "true"}, [game_id])
            )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    for r in results:
        r.pop("_raw", None)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(
            {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "calls": results},
            fh, ensure_ascii=False, indent=2,
        )
    return results
