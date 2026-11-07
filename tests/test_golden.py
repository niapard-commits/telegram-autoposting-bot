"""«Замок» на вигляд постів: точний текст кожного шаблону на фіксованих даних.

Якщо шаблон змінено свідомо, оновіть еталони: UPDATE_GOLDEN=1 pytest tests/test_golden.py
Випадкова зміна вигляду (наприклад, під час правки коду) зламає ці тести."""

import os
import re
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from lutserii import posts
from lutserii.dubs import Release
from lutserii.sources.anilist import AnimeInfo, Episode
from lutserii.sources.cheapshark import parse_deal
from lutserii.sources.gamerpower import Giveaway

GOLDEN = Path(__file__).parent / "golden"
KYIV = ZoneInfo("Europe/Kyiv")
SIG = "@lut_i_serii"
NOW = 1_761_624_000  # 2025-10-28 04:00 UTC


def deal(deal_id, title, sale, normal, savings, rating="62"):
    return parse_deal({"dealID": deal_id, "title": title, "salePrice": sale, "normalPrice": normal,
                       "savings": savings, "steamRatingPercent": rating})


def cases():
    d1 = deal("a%3D", "Suicide Squad: Kill the Justice League", "3.49", "69.99", "95")
    wd = [deal("1", "Watch Dogs", "2.00", "29.99", "93"), deal("2", "Watch Dogs 2", "5.00", "49.99", "90"),
          deal("3", "Watch_Dogs: Legion", "7.00", "59.99", "88")]
    giveaway = Giveaway(id=1, title="ANCIENT EVIL (IndieGala)", worth="$4.99", platforms="PC, DRM-Free",
                        end_date="2026-10-30 17:00:00", published="", url="https://g/1", image=None)
    released = [Release("Моє переродження в меч", 2, 4, team="Далі Буде", channel="DaliBudeDub"),
                Release("Монолог травниці", 3, 4, team="Далі Буде", channel="DaliBudeDub"),
                Release("Мистецтво Меча Онлайн", None, 22, team="Starfall", channel="starfall_ua")]
    upcoming = [Release("Чорна Конюшина", 2, 5, 6, "announced", "2026-10-28", "18:00", "Рідний Голос", channel="ridnyiholos")]
    ep = Episode(NOW + 3600, 4, "A Returner's Magic Should be Special Season 2", 90000, "https://anilist.co/anime/1",
                 genres=("Action", "Adventure", "Fantasy"),
                 streaming=(("Crunchyroll", "https://crunchyroll.com/x"), ("Bilibili TV", "https://bilibili.tv/x")))
    return {
        "deal": posts.deal_post(d1, "Epic Games Store", "У цьому шутері команда невдах має зробити неможливе.",
                                ("Бойовики",), SIG, "🇺🇦 ≈ ₴159 за курсом НБУ",
                                ("⏳ До 2 листопада, 18:00", "📉 Мінімум за весь час: $1.05")),
        "deal_series": posts.deal_series_post(
            wd, "Steam", "Watch Dogs", "Хакер перетворює місто на зброю.", ("Екшн",), SIG,
            {"1": "≈ ₴83", "2": "≈ ₴208", "3": "≈ ₴291"}, ("⏳ До 1 листопада, 20:00",)),
        "giveaway": posts.giveaway_post(giveaway, KYIV, "Двовимірна тактична рольова гра.", SIG, ("Пригоди", "Інді")),
        "dub_digest": posts.dub_digest(released, upcoming, date(2026, 10, 28), SIG, links=True),
        "dub_card": posts.dub_card(
            released[0], AnimeInfo(1, "c", genres=("Action", "Fantasy"), url="https://anilist.co/anime/139587"),
            "Меч-реінкарнація здобуває власну волю.", SIG, links=True),
        "anime_card": posts.anime_card(ep, KYIV, NOW, "Один із останніх магів повертається в минуле.", SIG),
    }


@pytest.mark.parametrize("name", ["deal", "deal_series", "giveaway", "dub_digest", "dub_card", "anime_card"])
def test_post_layout_is_locked(name):
    text = cases()[name]
    path = GOLDEN / f"{name}.txt"
    if os.environ.get("UPDATE_GOLDEN") or not path.exists():
        path.write_text(text + "\n", encoding="utf-8")
    assert text + "\n" == path.read_text(encoding="utf-8")


ALLOWED_TAG = re.compile(r"</?(?:b|s|i|blockquote)>|<a href=\"https://[^\"<>\s]+\">|</a>")


def test_hostile_data_never_injects_markup():
    evil = '<script>alert(1)</script> & "q" <a href="https://evil.example">x</a>'
    d = deal("x", evil, "1", "2", "50")
    giveaway = Giveaway(id=2, title=evil, worth=evil, platforms=evil, end_date="N/A", published="",
                        url='javascript:alert(1)"onmouseover="x', image=None)
    rel = Release(evil, 1, 1, team=evil, channel="ok_channel")
    ep = Episode(NOW, 1, evil, 1, "tg://resolve?domain=x", streaming=(("Crunchyroll", "javascript:alert(1)"),))
    texts = [
        posts.deal_post(d, evil, evil, (evil,), SIG, evil, (evil,)),
        posts.deal_series_post([d, d], evil, evil, evil, (evil,), SIG, {d.deal_id: evil}, (evil,)),
        posts.giveaway_post(giveaway, KYIV, evil, SIG, (evil,)),
        posts.dub_digest([rel], [rel], date(2026, 10, 28), SIG, links=True),
        posts.dub_card(rel, AnimeInfo(1, "c", genres=("Action",), url="http://insecure"), evil, SIG, links=True),
        posts.anime_card(ep, KYIV, NOW, evil, SIG),
        posts.anime_digest([ep], date(2026, 10, 28), KYIV, SIG),
    ]
    for text in texts:
        stripped = ALLOWED_TAG.sub("", text)
        assert "<" not in stripped and ">" not in stripped, text
        assert "javascript:" not in text and "tg://" not in text and "http://" not in text
        assert 'href="https://evil' not in text  # посилання з даних не стає живим


def test_safe_url_rules():
    assert posts.safe_url("https://store.steampowered.com/app/1")
    for bad in ("http://x.io", "javascript:alert(1)", "tg://x", "https://x.io/a b", 'https://x.io/"q', "", "https://x.io/<"):
        assert not posts.safe_url(bad)
