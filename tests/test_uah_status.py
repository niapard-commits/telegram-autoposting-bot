from conftest import FakeResponse

from lutserii import main as app
from lutserii.ai import GEMINI_API, Editor
from lutserii.sources import cheapshark, nbu, steam
from lutserii import posts

from test_main import raw_deal

NBU = [{"r030": 840, "txt": "Долар США", "rate": 41.5, "cc": "USD", "exchangedate": "28.10.2026"}]


def test_parse_rate():
    assert nbu.parse_rate(NBU) == 41.5
    assert nbu.parse_rate([{"cc": "EUR", "rate": 45}]) is None
    assert nbu.parse_rate([{"cc": "USD", "rate": "x"}]) is None
    assert nbu.parse_rate([{"cc": "USD", "rate": 0.5}]) is None
    assert nbu.parse_rate({}) is None


def test_steam_parses_ua_price():
    payload = {"1": {"success": True, "data": {
        "short_description": "d", "genres": [], "screenshots": [],
        "price_overview": {"discount_percent": 75, "final_formatted": "249₴", "initial_formatted": "999₴"}}}}
    info = steam.parse_app(payload, "1")
    assert (info.price_final, info.price_initial) == ("249₴", "999₴")
    payload["1"]["data"]["price_overview"] = {"discount_percent": 0, "final_formatted": "999₴",
                                                 "initial_formatted": ""}
    info = steam.parse_app(payload, "1")
    assert (info.price_final, info.price_initial) == ("999₴", "")
    payload["1"]["data"].pop("price_overview")
    assert steam.parse_app(payload, "1").price_final == ""


def test_uah_line_steam_exact_and_nbu_estimate(make_ctx):
    ctx, _, _ = make_ctx({nbu.API: NBU})
    deal = cheapshark.parse_deal(raw_deal("a", "G", sale="7.49"))
    assert app.uah_line(ctx, deal, "Steam", ("249₴", "999₴")) == "🇺🇦 У Steam України: 249₴ (було 999₴)"
    assert app.uah_line(ctx, deal, "Epic Games Store", ("249₴", "999₴")) == "🇺🇦 ≈ ₴311 за курсом НБУ"
    app.uah_line(ctx, deal, "GOG", ("", ""))
    assert len(ctx.session.calls) == 1  # курс береться один раз на добу


def test_uah_line_survives_nbu_failure(make_ctx):
    ctx, _, logs = make_ctx({nbu.API: FakeResponse([], 500)})
    deal = cheapshark.parse_deal(raw_deal("a", "G"))
    assert app.uah_line(ctx, deal, "GOG", ("", "")) == ""
    assert any("НБУ" in line for line in logs)


def test_deal_post_shows_uah_line():
    deal = cheapshark.parse_deal(raw_deal("a", "G", sale="7.49"))
    text = posts.deal_post(deal, "GOG", uah="🇺🇦 ≈ ₴311 за курсом НБУ")
    assert "≈ ₴311 за курсом НБУ" in text and "тепер ₴311" in text
    assert text.index("$7.49") < text.index("≈ ₴311") < text.index("🏪")


def test_ai_status_variants(make_ctx, db):
    ctx, _, _ = make_ctx({})
    assert "ШІ-редактор вимкнено" in app.ai_status(ctx)
    url = GEMINI_API.format(model="gm")
    ok = {"candidates": [{"content": {"parts": [{"text": "ок"}]}}]}
    ctx.editor = Editor(ctx.session.__class__({url: ok}), "k", "gm", db, provider="gemini", extra_keys=("k2",))
    assert "працює (Gemini, ключів: 2" in app.ai_status(ctx)
    ctx.editor = Editor(
        ctx.session.__class__({url: FakeResponse({}, 400)}), "SECRETKEY123", "gm", db, provider="gemini"
    )
    text = app.ai_status(ctx)
    assert "не вдався" in text and "код 400" in text
    assert "SECRETKEY123" not in text
