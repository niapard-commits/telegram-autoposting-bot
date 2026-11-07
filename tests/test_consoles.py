from datetime import datetime, timezone

from conftest import FakeResponse

from lutserii import main as app
from lutserii import posts
from lutserii.sources import consoles

NOW = datetime(2026, 10, 29, 10, 0, tzinfo=timezone.utc)


def rss(*items):
    body = "".join(
        f"<item><title><![CDATA[{t}]]></title><link>{link}</link><pubDate>{date}</pubDate>"
        f'<description><![CDATA[<img src="https://img.example/{i}.jpg">]]></description></item>'
        for i, (t, link, date) in enumerate(items)
    )
    return f"<rss><channel>{body}</channel></rss>"


PUB = "Wed, 28 Oct 2026 15:31:43 +0000"
OLD = "Mon, 05 Oct 2026 15:31:43 +0000"


def deal(title, game_id, plat="PS5", pct=65, old="2 399 ₴", new="839,65 ₴", plus=""):
    tail = f" / {plus} PS+" if plus else ""
    return (f"[{plat}] {title} −{pct}% ({old} → {new}{tail})",
            f"https://psprices.com/region-ua/game/{game_id}/slug?date=1", PUB)


def test_parse_deals_reads_prices_platforms_and_plus_marker():
    xml = rss(
        deal("Kingdom Come: Deliverance II Royal Edition", 1),
        deal("F1® 25", 2, pct=100, old="2 399 ₴", new="2 399 ₴", plus="0 ₴"),
        deal("Back 4 Blood - Доповнення", 3, plat="PS5 PS4", pct=100, old="489 ₴", new="0 ₴"),
        ("не знижка", "https://x.example/", PUB),
    )
    a, b, c = consoles.parse_deals(xml)
    assert (a.game_id, a.discount, a.price_old, a.price_new, a.family, a.store) == ("1", 65, 2399.0, 839.65, "PlayStation", "PS Store")
    assert a.url == "https://psprices.com/region-ua/game/1/slug" and a.image == "https://img.example/0.jpg"
    assert b.ps_plus_only and not b.free
    assert c.free and c.platforms == ("PS5", "PS4")


def test_parse_announcements():
    entries = consoles.parse_feed(rss(
        ("PlayStation Plus Monthly Games for November: Dead Island 2, Tomb Raider IV – VI Remastered, Monster Train 2", "https://b/1", PUB),
        ("Coming to Xbox Game Pass: EA Sports FC 26, RV There Yet?, and More", "https://b/2", PUB),
        ("Free Play Days – Hades II and Alan Wake 2", "https://b/3", PUB),
        ("Something else entirely", "https://b/4", PUB),
    ))
    ps, gp, fpd = consoles.parse_announcements(entries)
    assert (ps.kind, ps.label, len(ps.games)) == ("psplus", "November", 3)
    assert gp.kind == "gamepass" and gp.games == ("EA Sports FC 26", "RV There Yet?")
    assert fpd.kind == "freeplay" and fpd.games == ("Hades II", "Alan Wake 2")


def test_pick_console_deals_skips_junk_cheap_plus_and_repeats(db):
    from lutserii.config import Config
    xml = rss(
        deal("Big Game", 1, pct=70, old="2 000 ₴", new="600 ₴"),
        deal("Big Game Deluxe Edition", 2, pct=60, old="2 100 ₴", new="840 ₴"),  # те саме видання
        deal("Cheap Puzzle", 3, pct=80, old="100 ₴", new="20 ₴"),
        deal("Cool Game DLC Pack", 4, pct=70, old="900 ₴", new="270 ₴"),
        deal("Small Sale", 5, pct=20, old="2 000 ₴", new="1 600 ₴"),
        deal("Plus Only", 6, pct=50, old="1 000 ₴", new="500 ₴", plus="200 ₴"),
        deal("Second Game", 7, pct=50, old="1 000 ₴", new="500 ₴"),
    )
    picked = app.pick_console_deals(consoles.parse_deals(xml), Config(), db)
    assert [d.title for d in picked] == ["Big Game", "Second Game"]
    db.mark_posted("cdeal:7", "console-deal")
    assert [d.title for d in app.pick_console_deals(consoles.parse_deals(xml), Config(), db)] == ["Big Game"]


def test_console_posts_render():
    d = consoles.parse_deals(rss(deal("Game <X>", 1)))[0]
    text = posts.console_deal_post(d, "sig")
    assert "Game &lt;X&gt;" in text and "₴2 399" in text and "₴839,65" in text and "−65%" in text
    assert "#знижка #PlayStation" in text and "PSprices" in text
    free = consoles.parse_deals(rss(deal("Freebie", 2, pct=100, old="489 ₴", new="0 ₴")))[0]
    assert "безкоштовно" in posts.console_free_post(free)
    ann = consoles.parse_announcements(consoles.parse_feed(rss(
        ("PlayStation Plus Monthly Games for November: Alpha, Beta", "https://b/1", PUB))))[0]
    assert "у листопаді" in posts.subscription_post(ann) and "#PSPlus" in posts.subscription_post(ann)


def test_run_consoles_posts_subscription_free_and_best_deals_once(make_ctx):
    deals = rss(
        deal("Big Game", 1, pct=70, old="2 000 ₴", new="600 ₴"),
        deal("Second Game", 2, pct=50, old="1 000 ₴", new="500 ₴"),
        deal("Third Game", 3, pct=50, old="900 ₴", new="450 ₴"),
        deal("Freebie", 4, pct=100, old="489 ₴", new="0 ₴"),
    )
    blog = rss(
        ("PlayStation Plus Monthly Games for November: Alpha, Beta", "https://b/1", PUB),
        ("PlayStation Plus Monthly Games for October: Gamma, Delta", "https://b/0", OLD),
    )
    routes = {consoles.PSPRICES_RSS: deals, consoles.PS_BLOG_FEED: blog, **{u: rss() for u in consoles.XBOX_FEEDS}}
    ctx, sent, _ = make_ctx(routes, max_console_deals_per_run=2)
    ctx.clock = lambda: NOW.timestamp()
    assert app.run_consoles(ctx) == 4  # підписка + безкоштовне + 2 знижки (ліміт за запуск)
    assert "PlayStation Plus" in sent[0][0] and "безкоштовно" in sent[1][0]
    assert "Big Game" in sent[2][0] and "Second Game" in sent[3][0]
    assert app.run_consoles(ctx) == 1  # лишилася Third Game; решта вже була
    assert app.run_consoles(ctx) == 0


def test_tasks_run_in_blocks_anime_then_series_then_games():
    order = list(app.TASKS)
    assert order.index("anime") < order.index("dubs") < order.index("series") < order.index("free")
    assert order.index("free") < order.index("deals") < order.index("consoles")


def test_published_posts_are_recorded_for_shorts(make_ctx):
    from lutserii import published
    ctx, sent, _ = make_ctx()
    ctx.clock = lambda: NOW.timestamp()
    ctx.publish("anime-card", ["k1"], "🎬 <b>Показ</b>", ["https://img/1.jpg"])
    ctx.publish("deal", ["k2"], "🔥 знижка", [])
    ts = int(NOW.timestamp())
    assert [i["text"] for i in published.recent(ctx.db, "anime", ts - 100)] == ["🎬 <b>Показ</b>"]
    assert [i["kind"] for i in published.recent(ctx.db, "games", ts - 100)] == ["deal"]
    assert published.recent(ctx.db, "anime", ts + 1) == []
    # старе прибирається
    ctx.clock = lambda: NOW.timestamp() + 5 * 24 * 3600
    ctx.publish("deal", ["k3"], "ще знижка", [])
    assert published.recent(ctx.db, "anime", 0) == []
    assert [i["text"] for i in published.recent(ctx.db, "games", 0)] == ["ще знижка"]


def test_console_game_name_for_steam_lookup():
    from lutserii import main as app

    assert app.console_game_name("Exoprimal: видання Deluxe") == "Exoprimal"
    assert app.console_game_name("SCARLET NEXUS Deluxe Edition") == "SCARLET NEXUS"
    assert app.console_game_name("Hogwarts Legacy (PS5)") == "Hogwarts Legacy"
    assert app.console_game_name("Resident Evil Village Gold Edition") == "Resident Evil Village"
    assert app.console_game_name("Elden Ring") == "Elden Ring"


def test_anime_dub_cards_are_not_in_series_topic(db):
    from lutserii import published
    published.record(db, 1000, "dub-card", "🎙 <b>Оверґрід</b>\n#озвучка #аніме", ["p1"])
    published.record(db, 1000, "dub-card", "🎙 <b>Декстер</b>\n#озвучка #серіали", ["p2"])
    published.record(db, 1000, "dub", "🎙 Озвучка українською\n#озвучка #аніме #серіали", ["p3"])
    texts = [i["text"] for i in published.recent(db, "series", 0)]
    assert not any("Оверґрід" in t for t in texts)
    assert any("Декстер" in t for t in texts) and any("Озвучка українською" in t for t in texts)
