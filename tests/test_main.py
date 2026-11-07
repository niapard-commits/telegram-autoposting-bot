from datetime import datetime, timezone

from lutserii import main as app
from lutserii.config import Config
from lutserii.sources import anilist, cheapshark, gamerpower


def raw_deal(deal_id, title, rating="9.5", savings="80", sale="5.00", store="1"):
    return {"dealID": deal_id, "title": title, "salePrice": sale, "normalPrice": "25.00",
            "savings": savings, "dealRating": rating, "storeID": store}


def deal_routes(deals):
    return {
        f"{cheapshark.API}/stores": [{"storeID": "1", "storeName": "Steam"},
                                      {"storeID": "7", "storeName": "GOG"}],
        f"{cheapshark.API}/deals": deals,
    }


def test_pick_deals_filters_and_dedupes(db):
    cfg = Config()
    deals = [cheapshark.parse_deal(x) for x in [
        raw_deal("a", "Game A", rating="9.0"),
        raw_deal("b", "Game A", rating="9.8", store="7"),     # той самий тайтл, краща ціна
        raw_deal("c", "Low discount", savings="20"),
        raw_deal("d", "Low rating", rating="5.0"),
        raw_deal("e", "Free", sale="0.00"),
        raw_deal("f", "Game F", rating="8.5"),
    ]]
    picked = app.pick_deals(deals, cfg, db)
    assert [d.deal_id for d in picked] == ["b", "f"]


def test_filter_trusted_drops_key_resellers():
    stores = {"1": "Steam", "7": "GOG", "99": "DreamGame"}
    deals = [cheapshark.parse_deal(raw_deal(str(i), f"G{i}", store=s))
             for i, s in enumerate(["1", "99", "7", "404"])]
    logs = []
    kept = app.filter_trusted(deals, stores, Config(), logs.append)
    assert [d.store_id for d in kept] == ["1", "7"]
    assert "DreamGame" in logs[0]


def test_filter_trusted_empty_list_disables_filter():
    deals = [cheapshark.parse_deal(raw_deal("a", "G", store="99"))]
    cfg = Config(trusted_stores=())
    assert app.filter_trusted(deals, {"99": "DreamGame"}, cfg) == deals


def test_trusted_stores_from_env(monkeypatch):
    monkeypatch.setenv("TRUSTED_STORES", "Steam, GOG ,")
    assert Config.from_env().trusted_stores == ("Steam", "GOG")
    monkeypatch.setenv("TRUSTED_STORES", "")
    assert Config.from_env().trusted_stores == ()


def test_pick_deals_skips_recent(db):
    db.mark_posted("title:game a", "deal-alias")
    deals = [cheapshark.parse_deal(raw_deal("a", "Game A"))]
    assert app.pick_deals(deals, Config(), db) == []


def test_run_deals_respects_run_and_day_budget(make_ctx):
    deals = [raw_deal(str(i), ["Alpha Quest", "Bravo Run", "Charlie Hunt", "Delta Force", "Echo Base"][i], rating=str(9.9 - i / 10)) for i in range(5)]
    ctx, sent, _ = make_ctx(deal_routes(deals), max_deals_per_run=2, max_deals_per_day=3)
    assert app.run_deals(ctx) == 2
    assert app.run_deals(ctx) == 1  # у день ліміт 3
    assert app.run_deals(ctx) == 0
    assert len(sent) == 3
    assert "Steam" in sent[0][0]
    # повторний запуск не дублює вже опубліковане
    titles = [text.split("\n")[0] for text, _ in sent]
    assert len(set(titles)) == 3


def test_run_deals_dry_run_sends_nothing(make_ctx):
    ctx, sent, logs = make_ctx(deal_routes([raw_deal("1", "Game")]), dry_run=True)
    assert app.run_deals(ctx) == 1
    assert sent == []
    assert any("DRY RUN" in line for line in logs)
    assert not ctx.db.was_posted("deal:1")


def giveaway(i, published):
    return {"id": i, "title": f"Game {i} Giveaway", "status": "Active",
            "published_date": published, "open_giveaway_url": f"https://g/{i}"}


def test_run_free_first_run_limits_and_skips_old(make_ctx):
    items = [giveaway(i, f"2026-10-{10 + i} 10:00:00") for i in range(1, 7)]
    ctx, sent, _ = make_ctx({gamerpower.API: items}, max_free_per_run=3)
    assert app.run_free(ctx) == 3
    assert len(sent) == 3
    assert "Game 6" in sent[0][0]  # найновіші першими
    # старі позначені, тож другий запуск нічого нового не публікує
    assert app.run_free(ctx) == 0


def test_run_free_later_run_posts_new(make_ctx):
    ctx, sent, _ = make_ctx({gamerpower.API: [giveaway(1, "2026-10-10 10:00:00")]})
    app.run_free(ctx)
    ctx.session.routes[gamerpower.API] = [
        giveaway(1, "2026-10-10 10:00:00"), giveaway(2, "2026-10-12 10:00:00")]
    assert app.run_free(ctx) == 1
    assert len(sent) == 2


def anime_item(ts, ep, popularity, title):
    return {"airingAt": ts, "episode": ep, "media": {
        "id": ep, "siteUrl": "https://anilist.co/anime/1", "format": "TV",
        "countryOfOrigin": "JP", "isAdult": False, "popularity": popularity,
        "title": {"romaji": title, "english": None, "native": None}}}


def test_run_anime_once_per_day(make_ctx):
    now = datetime(2026, 10, 27, 5, 0, tzinfo=timezone.utc).timestamp()
    page = {"data": {"Page": {"pageInfo": {"hasNextPage": False}, "airingSchedules": [
        anime_item(int(now) + 7200, 3, 90000, "Big Show"),
        anime_item(int(now) + 3600, 1, 30000, "Mid Show"),
        anime_item(int(now) + 1800, 2, 100, "Obscure Show"),
    ]}}}
    ctx, sent, _ = make_ctx({anilist.API: page}, anime_digest=True)
    ctx.clock = lambda: now
    assert app.run_anime(ctx) == 2
    text = sent[0][0]
    assert "Obscure" not in text
    assert text.index("Mid Show") < text.index("Big Show")  # за часом виходу
    assert app.run_anime(ctx) == 0  # вдруге за день не публікуємо


def test_run_anime_digest_off_by_default(make_ctx):
    now = datetime(2026, 10, 27, 5, 0, tzinfo=timezone.utc).timestamp()
    page = {"data": {"Page": {"pageInfo": {"hasNextPage": False}, "airingSchedules": [
        anime_item(int(now) + 7200, 3, 90000, "Big Show"),
    ]}}}
    ctx, sent, _ = make_ctx({anilist.API: page})
    ctx.clock = lambda: now
    assert app.run_anime(ctx) == 1  # лише картка, без списку «Аніме сьогодні»
    assert len(sent) == 1 and "Аніме сьогодні" not in sent[0][0]


def test_run_anime_nothing_popular(make_ctx):
    page = {"data": {"Page": {"pageInfo": {"hasNextPage": False}, "airingSchedules": []}}}
    ctx, sent, _ = make_ctx({anilist.API: page})
    assert app.run_anime(ctx) == 0
    assert sent == []


def test_run_task_continues_after_failure(make_ctx):
    ctx, sent, _ = make_ctx({gamerpower.API: [giveaway(1, "2026-10-10 10:00:00")]})
    ctx.clock = lambda: datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc).timestamp()  # вівторок: п'ятнична добірка не стартує
    # у маршрутах немає CheapShark та AniList: ці завдання впадуть, роздачі мають відпрацювати
    errors = app.run_task(ctx, "all")
    assert len(errors) == 3  # аніме, знижки, консолі
    assert len(sent) == 1


def test_run_task_test_message(make_ctx):
    ctx, sent, _ = make_ctx()
    assert app.run_task(ctx, "test") == []
    assert "Бот підключено" in sent[0][0]


def test_main_requires_credentials(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert app.main(["test"]) == 2


def test_task_groups_are_valid_tasks():
    assert set(app.GROUPS) == {"morning", "games", "evening"}
    for names in app.GROUPS.values():
        assert all(n in app.TASKS for n in names)
