"""Знижки на ігри: CheapShark (безкоштовний, без ключа)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

API = "https://www.cheapshark.com/api/1.0"
REDIRECT = "https://www.cheapshark.com/redirect"
TIMEOUT = 20


@dataclass(frozen=True)
class Deal:
    deal_id: str
    title: str
    sale_price: float
    normal_price: float
    savings: float  # відсоток знижки, 0-100
    rating: float  # оцінка вигідності від CheapShark, 0-10
    store_id: str
    steam_app_id: str | None
    steam_rating: int | None
    thumb: str | None

    @property
    def url(self) -> str:
        # У відповіді API dealID уже закодований у відсотках (%2B, %3D).
        deal_id = self.deal_id if "%" in self.deal_id else quote(self.deal_id, safe="")
        return f"{REDIRECT}?dealID={deal_id}"

    @property
    def image(self) -> str | None:
        if self.steam_app_id:
            return (
                "https://cdn.cloudflare.steamstatic.com/steam/apps/"
                f"{self.steam_app_id}/header.jpg"
            )
        return self.thumb


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _positive_int(value: Any) -> int | None:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def parse_deal(raw: dict) -> Deal:
    app_id = _positive_int(raw.get("steamAppID"))
    return Deal(
        deal_id=str(raw.get("dealID", "")),
        title=str(raw.get("title", "")).strip(),
        sale_price=_float(raw.get("salePrice")),
        normal_price=_float(raw.get("normalPrice")),
        savings=_float(raw.get("savings")),
        rating=_float(raw.get("dealRating")),
        store_id=str(raw.get("storeID", "")),
        steam_app_id=str(app_id) if app_id else None,
        steam_rating=_positive_int(raw.get("steamRatingPercent")),
        thumb=raw.get("thumb") or None,
    )


def fetch_stores(session: Any) -> dict[str, str]:
    """storeID -> назва магазину."""
    resp = session.get(f"{API}/stores", timeout=TIMEOUT)
    resp.raise_for_status()
    return {str(s.get("storeID")): str(s.get("storeName", "")) for s in resp.json()}


def fetch_deals(session: Any, page_size: int = 60, upper_price: int = 60) -> list[Deal]:
    resp = session.get(
        f"{API}/deals",
        params={
            "onSale": 1,
            "pageSize": page_size,
            "upperPrice": upper_price,
            "sortBy": "Deal Rating",
        },
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    deals = [parse_deal(item) for item in resp.json()]
    return [d for d in deals if d.deal_id and d.title]


# «Deal Rating» майже в усіх кращих знижок дорівнює 10, тож одна сторінка за цим сортуванням швидко вичерпується
# (усе вже опубліковано). Беремо ще й за відгуками, розміром знижки та новизною.
WIDE_QUERIES = (("Deal Rating", 0), ("Deal Rating", 1), ("Reviews", 0), ("Savings", 0), ("Recent", 0))


def fetch_deals_wide(
    session: Any, store_ids: list[str] | None = None, page_size: int = 60, upper_price: int = 60
) -> list[Deal]:
    """Знижки з кількох сортувань і сторінок без повторів. Збій окремого запиту решту не ламає."""
    out: dict[str, Deal] = {}
    last_error: Exception | None = None
    for sort, page in WIDE_QUERIES:
        params: dict[str, Any] = {
            "onSale": 1, "pageSize": page_size, "pageNumber": page, "upperPrice": upper_price, "sortBy": sort,
        }
        if store_ids:
            params["storeID"] = ",".join(store_ids)
        try:
            resp = session.get(f"{API}/deals", params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            items = resp.json()
        except Exception as exc:  # один запит не повинен ламати решту
            last_error = exc
            continue
        for item in items:
            deal = parse_deal(item)
            if deal.deal_id and deal.title and deal.deal_id not in out:
                out[deal.deal_id] = deal
    if not out and last_error is not None:
        raise last_error
    return list(out.values())
