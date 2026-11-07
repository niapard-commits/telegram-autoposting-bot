from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from lutserii import main, weekly
from lutserii.dubs import Release
from lutserii.sources import cheapshark, gamerpower

TZ = ZoneInfo("Europe/Kyiv")
# п'ятниця 30 жовтня 2026, 19:00 за Києвом
FRIDAY = int(datetime(2026, 10, 30, 17, 0, tzinfo=timezone.utc).timestamp())
THURSDAY = FRIDAY - 86400


def _deal(i, title, sale, savings, rating):
    return cheapshark.Deal(f"id{i}", title, sale, round(sale / (1 - savings / 100), 2), savings, rating, "1", None, None, None)


def _fill(db):
    t = FRIDAY - 2 * 86400
    weekly.record_deal(db, t, _deal(1, "Hades", 12.49, 50, 9.1), "Steam", "≈ ₴569")
    weekly.record_deal(db, t, _deal(2, "Celeste", 4.99, 75, 8.4), "GOG", "")
    weekly.record_deal(db, t - 86400 * 10, _deal(3, "Давня гра", 1.0, 90, 9.9), "Steam")  # старша за тиждень
    weekly.record_free(db, t, gamerpower.Giveaway(1, "Free Game", "$20", "PC", "2026-11-02 17:00:00", "", "https://g/1", None))
    weekly.record_free(db, t, gamerpower.Giveaway(2, "Ended Game", "$20", "PC", "2026-10-29 10:00:00", "", "https://g/2", None))
    weekly.record_dub(db, t, Release("Тайсон", team="OtherTeam", channel="", site="OtherTeam"))
    weekly.record_dub(db, t, Release("Флеш", 1, 8, team="DubTeam", channel="dubteam"))
    weekly.record_dub(db, t, Release("Брати", None, 7, team="DubTeam", channel="dubteam"))


def test_build_text_has_three_sections_and_skips_stale_items(db):
    _fill(db)
    text = weekly.build_text(weekly.load(db, FRIDAY), FRIDAY, TZ, "@lut_i_serii")
    assert "Підсумки тижня · 24–30 жовтня" in text
    assert "Hades" in text and "−50%" in text and "$12.49" in text and "≈ ₴569" in text and "Steam" in text
    assert text.index("Hades") < text.index("Celeste")  # вища оцінка першою
    assert "Давня гра" not in text
    assert "Free Game" in text and "до 2 листопада" in text and "Ended Game" not in text
    assert '<a href="https://t.me/dubteam"><b>DubTeam</b></a>' in text and "Флеш, Брати" in text
    assert "Тайсон" in text and "#підсумкитижня" in text and text.endswith("@lut_i_serii")
    assert len(text) < 4096


def test_build_text_is_none_without_facts(db):
    assert weekly.build_text(weekly.load(db, FRIDAY), FRIDAY, TZ) is None


def test_load_prunes_old_records(db):
    weekly.record_deal(db, FRIDAY - 30 * 86400, _deal(9, "Old", 1.0, 80, 9), "Steam")
    assert weekly.load(db, FRIDAY)["deal"] == [] and db.cache_items("week:") == {}


def test_run_weekly_only_on_friday_evening_and_once(make_ctx):
    ctx, sent, _ = make_ctx({})
    _fill(ctx.db)
    ctx.clock = lambda: THURSDAY
    assert main.run_weekly(ctx) == 0 and sent == []  # четвер
    ctx.clock = lambda: int(datetime(2026, 10, 30, 10, 0, tzinfo=timezone.utc).timestamp())
    assert main.run_weekly(ctx) == 0  # п'ятниця, але ще день
    ctx.clock = lambda: FRIDAY
    assert main.run_weekly(ctx) == 1 and len(sent) == 1 and "Підсумки тижня" in sent[0][0]
    assert main.run_weekly(ctx) == 0 and len(sent) == 1  # вдруге за тиждень не публікуємо


def test_run_weekly_force_ignores_day(make_ctx):
    ctx, sent, _ = make_ctx({}, force=True)
    _fill(ctx.db)
    ctx.clock = lambda: THURSDAY
    assert main.run_weekly(ctx) == 1 and len(sent) == 1


def test_published_deals_are_recorded_for_the_week(make_ctx, monkeypatch):
    ctx, sent, _ = make_ctx({})
    ctx.clock = lambda: FRIDAY
    d = _deal(5, "Portal", 1.99, 80, 9.5)
    monkeypatch.setattr(cheapshark, "fetch_stores", lambda s: {"1": "Steam"})
    monkeypatch.setattr(cheapshark, "fetch_deals_wide", lambda s, ids=None: [d])
    monkeypatch.setattr(main, "deal_post_text", lambda c, g, store: ("текст", [], None))
    monkeypatch.setattr(main, "uah_approx", lambda c, p: "≈ ₴91")
    assert main.run_deals(ctx) == 1
    items = weekly.load(ctx.db, FRIDAY)["deal"]
    assert items and items[0][1]["title"] == "Portal" and items[0][1]["uah"] == "≈ ₴91"
