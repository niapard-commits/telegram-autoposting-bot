from datetime import datetime, timezone

from conftest import FakeResponse

from lutserii import main as app
from lutserii.sources import anilist, cheapshark, steam
from lutserii.textutil import base_title, norm_title


def test_base_title_strips_editions():
    assert base_title("Back 4 Blood: Ultimate Edition") == "Back 4 Blood"
    assert base_title("Cyberpunk 2077 - Gold Edition") == "Cyberpunk 2077"
    assert base_title("Game Digital Deluxe Edition") == "Game"
    assert base_title("The Witcher 3: Game of the Year Edition") == "The Witcher 3"
    assert base_title("Deluxe Edition") == "Deluxe Edition"  # лишаємо, щоб не отримати порожнє
    assert base_title("Plain Game") == "Plain Game"
    assert norm_title("Back 4 Blood!") == "back4blood"


def test_pick_app_id_matches_base_game_only():
    payload = {"items": [
        {"type": "app", "name": "Back 4 Blood - Tunnel of Blood", "id": 1},
        {"type": "sub", "name": "Back 4 Blood", "id": 2},
        {"type": "app", "name": "Back 4 Blood", "id": 924970},
    ]}
    assert steam.pick_app_id(payload, "Back 4 Blood: Ultimate Edition") == "924970"
    assert steam.pick_app_id(payload, "Another Game") is None
    assert steam.pick_app_id({}, "Back 4 Blood") is None
    assert steam.pick_app_id([], "Back 4 Blood") is None


RAW = {"dealID": "d1", "title": "Back 4 Blood: Ultimate Edition", "salePrice": "8", "normalPrice": "99",
       "savings": "92", "dealRating": "9.5", "storeID": "3", "steamAppID": None}


def routes(search_payload):
    return {
        f"{cheapshark.API}/stores": [{"storeID": "3", "storeName": "GreenManGaming"}],
        f"{cheapshark.API}/deals": [RAW],
        steam.SEARCH_API: search_payload,
        steam.API: {"924970": {"success": True, "data": {
            "short_description": "Кооперативна гра про зомбі.",
            "genres": [{"description": "Екшн"}],
            "screenshots": [{"path_full": "https://cdn/s1.jpg"}, {"path_full": "https://cdn/s2.jpg"}]}}},
    }


def test_run_deals_finds_steam_page_by_title(make_ctx):
    ctx, sent, _ = make_ctx(routes({"items": [{"type": "app", "name": "Back 4 Blood", "id": 924970}]}))
    assert app.run_deals(ctx) == 1
    text, photos = sent[0]
    assert "Кооперативна гра про зомбі." in text and "Екшн" in text
    assert photos[0].endswith("/924970/header.jpg") and len(photos) == 3
    assert ctx.db.get_cache("steam-id:back4blood") == "924970"


def test_not_found_is_cached_and_not_searched_again(make_ctx):
    ctx, sent, _ = make_ctx(routes({"items": []}))
    assert app.run_deals(ctx) == 1
    assert "Кооперативна" not in sent[0][0]  # опису немає, але пост вийшов
    assert ctx.db.get_cache("steam-id:back4blood") == "-"
    searches = [c for c in ctx.session.calls if c[0] == steam.SEARCH_API]
    assert len(searches) == 1
    assert app.find_steam_app(ctx, "Back 4 Blood: Deluxe Edition") is None
    assert len([c for c in ctx.session.calls if c[0] == steam.SEARCH_API]) == 1


def test_search_failure_is_not_cached(make_ctx):
    ctx, sent, logs = make_ctx(routes(FakeResponse({}, 500)))
    assert app.run_deals(ctx) == 1
    assert ctx.db.get_cache("steam-id:back4blood") is None
    assert any("пошук за назвою не вдався" in line for line in logs)


def test_force_reposts_anime_digest(make_ctx):
    now = datetime(2026, 10, 27, 5, 0, tzinfo=timezone.utc).timestamp()
    page = {"data": {"Page": {"pageInfo": {"hasNextPage": False}, "airingSchedules": [
        {"airingAt": int(now) + 3600, "episode": 1, "media": {
            "id": 1, "siteUrl": "u", "format": "TV", "countryOfOrigin": "JP", "isAdult": False,
            "popularity": 90000, "title": {"romaji": "Show", "english": None, "native": None}}}]}}}
    ctx, sent, _ = make_ctx({anilist.API: page}, force=True, anime_digest=True)
    ctx.clock = lambda: now
    assert app.run_anime(ctx) == 1
    assert app.run_anime(ctx) == 1  # force: вдруге за день теж публікуємо
    assert len(sent) == 4  # двічі добірка й картка
