from conftest import FakeResponse, FakeSession

from lutserii.sources import anilist, cheapshark, gamerpower

RAW_DEAL = {
    "dealID": "abc%2Bdef%3D",
    "title": "Cool Game",
    "salePrice": "9.99",
    "normalPrice": "39.99",
    "savings": "75.018759",
    "dealRating": "9.4",
    "storeID": "1",
    "steamAppID": "12345",
    "steamRatingPercent": "92",
    "thumb": "https://example.com/t.jpg",
}


def test_parse_deal():
    deal = cheapshark.parse_deal(RAW_DEAL)
    assert deal.title == "Cool Game"
    assert deal.sale_price == 9.99
    assert round(deal.savings) == 75
    assert deal.steam_rating == 92
    assert deal.url == "https://www.cheapshark.com/redirect?dealID=abc%2Bdef%3D"
    assert deal.image.endswith("/steam/apps/12345/header.jpg")


def test_parse_deal_missing_fields():
    deal = cheapshark.parse_deal(
        {"dealID": "x y", "title": "T", "steamAppID": "0", "steamRatingPercent": "0"}
    )
    assert deal.steam_app_id is None and deal.steam_rating is None
    assert deal.sale_price == 0.0
    assert deal.url.endswith("dealID=x%20y")
    assert deal.image is None


def test_fetch_stores_and_deals():
    session = FakeSession(
        {
            f"{cheapshark.API}/stores": [{"storeID": "1", "storeName": "Steam"}],
            f"{cheapshark.API}/deals": [RAW_DEAL, {"dealID": "", "title": "Bad"}],
        }
    )
    assert cheapshark.fetch_stores(session) == {"1": "Steam"}
    deals = cheapshark.fetch_deals(session)
    assert [d.title for d in deals] == ["Cool Game"]


def test_gamerpower_filters_and_sorts():
    data = [
        {"id": 1, "title": "Old Game Giveaway", "status": "Active",
         "published_date": "2026-10-01 10:00:00", "open_giveaway_url": "https://g/1",
         "worth": "$9.99", "platforms": "PC, Steam", "end_date": "N/A"},
        {"id": 2, "title": "New Game (Epic) Giveaway", "status": "Active",
         "published_date": "2026-10-20 10:00:00", "open_giveaway_url": "https://g/2"},
        {"id": 3, "title": "Expired", "status": "Expired", "open_giveaway_url": "https://g/3"},
    ]
    session = FakeSession({gamerpower.API: data})
    items = gamerpower.fetch_giveaways(session)
    assert [g.id for g in items] == [2, 1]
    assert items[1].title == "Old Game"


def test_gamerpower_empty_and_envelope():
    assert gamerpower.fetch_giveaways(FakeSession({gamerpower.API: FakeResponse({}, 201)})) == []
    envelope = {"status": 0, "status_message": "error"}
    assert gamerpower.fetch_giveaways(FakeSession({gamerpower.API: envelope})) == []


def _item(ts, ep, popularity=50000, **media_overrides):
    media = {
        "id": 1,
        "siteUrl": "https://anilist.co/anime/1",
        "format": "TV",
        "countryOfOrigin": "JP",
        "isAdult": False,
        "popularity": popularity,
        "title": {"romaji": "Romaji Title", "english": None, "native": "日本語"},
    }
    media.update(media_overrides)
    return {"airingAt": ts, "episode": ep, "media": media}


def test_anilist_paginates_and_filters():
    pages = {
        1: {"data": {"Page": {"pageInfo": {"hasNextPage": True},
            "airingSchedules": [_item(100, 1), _item(110, 2, isAdult=True)]}}},
        2: {"data": {"Page": {"pageInfo": {"hasNextPage": False},
            "airingSchedules": [_item(120, 3, countryOfOrigin="KR"),
                                _item(130, 4, format="MOVIE"),
                                _item(140, 5, title={"romaji": None, "english": "English", "native": None})]}}},
    }
    session = FakeSession({anilist.API: lambda payload: pages[payload["variables"]["page"]]})
    episodes = anilist.fetch_airing(session, 0, 1000)
    assert [(e.episode, e.title) for e in episodes] == [(1, "Romaji Title"), (5, "English")]
    assert len(session.calls) == 2


def test_anilist_error_payload():
    session = FakeSession({anilist.API: {"errors": [{"message": "Too many requests"}]}})
    try:
        anilist.fetch_airing(session, 0, 1)
    except RuntimeError as exc:
        assert "Too many requests" in str(exc)
    else:
        raise AssertionError("очікували RuntimeError")


def test_fetch_deals_wide_merges_queries_and_survives_failures():
    calls = []

    def route(params):
        calls.append(params)
        if params["sortBy"] == "Savings":
            raise RuntimeError("boom")
        return [RAW_DEAL]

    session = FakeSession({f"{cheapshark.API}/deals": route})
    deals = cheapshark.fetch_deals_wide(session, ["1", "7"])
    assert [d.title for d in deals] == ["Cool Game"]
    assert len(calls) == len(cheapshark.WIDE_QUERIES)
    assert all(c["storeID"] == "1,7" for c in calls)
