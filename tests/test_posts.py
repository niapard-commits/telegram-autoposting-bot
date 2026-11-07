from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from lutserii import posts
from lutserii.sources.anilist import Episode
from lutserii.sources.cheapshark import parse_deal
from lutserii.sources.gamerpower import Giveaway

KYIV = ZoneInfo("Europe/Kyiv")


def test_deal_post_escapes_and_formats():
    deal = parse_deal(
        {"dealID": "id%3D", "title": "Fish & <Chips>", "salePrice": "4.99",
         "normalPrice": "19.99", "savings": "75", "steamRatingPercent": "88"}
    )
    text = posts.deal_post(deal, "Epic Games Store")
    assert "Fish &amp; &lt;Chips&gt;" in text
    assert "$4.99" in text and "$19.99" in text and "−75%" in text
    assert "Відгуки в Steam: 88%" in text
    assert "#знижка #EpicGamesStore" in text
    assert "https://www.cheapshark.com/redirect?dealID=id%3D" in text


def test_deal_post_without_rating_or_store():
    deal = parse_deal({"dealID": "x", "title": "T", "salePrice": "1", "normalPrice": "2", "savings": "50"})
    text = posts.deal_post(deal, "")
    assert "Відгуки" not in text
    assert "Магазин" in text


def test_giveaway_post():
    giveaway = Giveaway(
        id=1, title="Free Game", worth="$19.99", platforms="PC, Steam",
        end_date="2026-10-30 17:00:00", published="", url="https://g/1", image=None,
    )
    text = posts.giveaway_post(giveaway, KYIV)
    assert "Безкоштовно: Free Game" in text
    assert "$19.99" in text
    assert "До 30 жовтня, 19:00" in text  # 17:00 UTC = 19:00 за київським часом
    assert "#роздача" in text


def test_giveaway_post_without_optional_fields():
    giveaway = Giveaway(id=1, title="G", worth="N/A", platforms="", end_date="N/A",
                        published="", url="https://g/1", image=None)
    text = posts.giveaway_post(giveaway, KYIV)
    assert "Зазвичай" not in text and "До " not in text


def test_anime_digest_uses_local_time():
    ts = int(datetime(2026, 10, 27, 16, 30, tzinfo=timezone.utc).timestamp())
    episodes = [Episode(ts, 5, "Show <1>", 50000, "https://anilist.co/anime/1")]
    text = posts.anime_digest(episodes, date(2026, 10, 27), KYIV)
    assert "27 жовтня" in text
    assert "18:30" in text  # жовтневий час після переходу на зимовий (UTC+2)
    assert "Show &lt;1&gt;" in text and "серія 5" in text
