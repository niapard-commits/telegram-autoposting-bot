"""IsThereAnyDeal: дата завершення знижки та історичний мінімум ціни (потрібен ключ API)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..textutil import norm_title

BASE = "https://api.isthereanydeal.com"
TIMEOUT = 20


@dataclass(frozen=True)
class ItadInfo:
    history_low: float | None  # найнижча ціна за весь час, USD
    expiry: datetime | None  # коли закінчується знижка (з часовим поясом) або None
    price: float | None  # ціна цієї знижки в ITAD, USD


def store_key(name: str) -> str:
    """Назви магазинів у CheapShark та ITAD трохи різняться («Epic Games Store» / «Epic Game Store»)."""
    return norm_title(name).replace("epicgamesstore", "epicgamestore")


def lookup_game(session: Any, key: str, title: str, appid: str | None = None) -> str | None:
    """Ідентифікатор гри в ITAD: за номером Steam, інакше за назвою."""
    for params in ([{"appid": appid}] if appid else []) + [{"title": title}]:
        resp = session.get(f"{BASE}/games/lookup/v1", params={"key": key, **params}, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        game = data.get("game") if isinstance(data, dict) and data.get("found") else None
        if isinstance(game, dict) and isinstance(game.get("id"), str):
            return game["id"]
    return None


def fetch_prices(session: Any, key: str, game_id: str) -> Any:
    resp = session.post(
        f"{BASE}/games/prices/v3",
        params={"key": key, "country": "UA", "nondeals": "true"},
        json=[game_id],
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def _amount(node: Any) -> float | None:
    if isinstance(node, dict) and node.get("currency") == "USD":
        try:
            return float(node["amount"])
        except (KeyError, TypeError, ValueError):
            return None
    return None


def parse_info(payload: Any, store_name: str) -> ItadInfo | None:
    """Мінімум за весь час і дата завершення саме тієї знижки, що в нашому магазині."""
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        return None
    entry = payload[0]
    low = _amount((entry.get("historyLow") or {}).get("all"))
    expiry = price = None
    wanted = store_key(store_name)
    for deal in entry.get("deals") or []:
        if not isinstance(deal, dict) or store_key(str((deal.get("shop") or {}).get("name", ""))) != wanted:
            continue
        price = _amount(deal.get("price"))
        raw = deal.get("expiry")
        if isinstance(raw, str):
            try:
                expiry = datetime.fromisoformat(raw)
            except ValueError:
                expiry = None
        break
    if low is None and expiry is None:
        return None
    return ItadInfo(low, expiry, price)
