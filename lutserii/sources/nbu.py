"""Офіційний курс долара до гривні від НБУ (без ключа): для приблизної ціни в гривнях."""

from __future__ import annotations

from typing import Any

API = "https://bank.gov.ua/NBUStatService/v1/statdirectory/exchange"
TIMEOUT = 15


def parse_rate(payload: Any) -> float | None:
    if not isinstance(payload, list):
        return None
    for item in payload:
        if isinstance(item, dict) and item.get("cc") == "USD":
            try:
                rate = float(item.get("rate"))
            except (TypeError, ValueError):
                return None
            return rate if 10 < rate < 200 else None  # відсікаємо явне сміття
    return None


def fetch_usd_rate(session: Any) -> float | None:
    resp = session.get(API, params={"valcode": "USD", "json": ""}, timeout=TIMEOUT)
    resp.raise_for_status()
    return parse_rate(resp.json())
