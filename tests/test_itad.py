from datetime import datetime, timezone

from conftest import FakeResponse

from lutserii import main as app
from lutserii import posts
from lutserii.sources import cheapshark, itad

from test_main import raw_deal

PRICES = [{
    "id": "g1",
    "historyLow": {"all": {"amount": 11.04, "amountInt": 1104, "currency": "USD"},
                   "y1": {"amount": 54.59, "currency": "USD"}},
    "deals": [
        {"shop": {"id": 6, "name": "Fanatical"}, "price": {"amount": 55.63, "currency": "USD"},
         "regular": {"amount": 64.99, "currency": "USD"}, "cut": 14,
         "expiry": "2026-10-31T17:00:00+01:00"},
        {"shop": {"id": 16, "name": "Epic Game Store"}, "price": {"amount": 3.49, "currency": "USD"},
         "cut": 95, "expiry": None},
    ],
}]
LOOKUP = {"found": True, "game": {"id": "g1", "title": "X"}}
NOW = datetime(2026, 10, 28, 6, 0, tzinfo=timezone.utc)


def test_parse_info_picks_matching_store():
    info = itad.parse_info(PRICES, "Fanatical")
    assert info.history_low == 11.04 and info.price == 55.63
    assert info.expiry == datetime.fromisoformat("2026-10-31T17:00:00+01:00")
    epic = itad.parse_info(PRICES, "Epic Games Store")  # назви магазинів різняться написанням
    assert epic.price == 3.49 and epic.expiry is None


def test_parse_info_edge_cases():
    assert itad.parse_info([], "Steam") is None
    assert itad.parse_info({"x": 1}, "Steam") is None
    only_low = itad.parse_info(PRICES, "GOG")  # магазину немає: все одно мінімум за весь час
    assert only_low.history_low == 11.04 and only_low.expiry is None and only_low.price is None
    eur = [{"historyLow": {"all": {"amount": 5, "currency": "EUR"}}, "deals": []}]
    assert itad.parse_info(eur, "Steam") is None  # не USD: не порівнюємо


def test_lookup_prefers_appid_then_title():
    class S:
        def __init__(self):
            self.params = []

        def get(self, url, params=None, timeout=None, **kw):
            self.params.append(params)
            found = "title" in params
            return FakeResponse(LOOKUP if found else {"found": False})

    s = S()
    assert itad.lookup_game(s, "K", "Portal 2", "620") == "g1"
    assert [list(p)[1] for p in s.params] == ["appid", "title"]


def routes(deal_price="3.49"):
    return {
        f"{itad.BASE}/games/lookup/v1": LOOKUP,
        f"{itad.BASE}/games/prices/v3": PRICES,
    }


def test_itad_extras_expiry_and_history_low(make_ctx):
    ctx, _, _ = make_ctx(routes(), itad_api_key="K")
    ctx.clock = lambda: NOW.timestamp()
    fan = cheapshark.parse_deal(raw_deal("a", "Game", sale="55.63"))
    lines = app.itad_extras(ctx, fan, "Fanatical")
    assert lines == ("⏳ До 31 жовтня, 18:00", "📉 Мінімум за весь час: $11.04")  # час за Києвом
    epic = cheapshark.parse_deal(raw_deal("b", "Game", sale="11.00"))
    assert app.itad_extras(ctx, epic, "GOG")[-1] == "🏆 Історичний мінімум ціни"


def test_itad_extras_without_key_or_on_failure(make_ctx):
    ctx, _, logs = make_ctx({}, itad_api_key="")
    deal = cheapshark.parse_deal(raw_deal("a", "Game"))
    assert app.itad_extras(ctx, deal, "Steam") == ()
    ctx, _, logs = make_ctx({f"{itad.BASE}/games/lookup/v1": FakeResponse({}, 500)}, itad_api_key="K")
    assert app.itad_extras(ctx, deal, "Steam") == () and any("ITAD" in l for l in logs)


def test_lookup_not_found_is_cached(make_ctx):
    ctx, _, _ = make_ctx({f"{itad.BASE}/games/lookup/v1": {"found": False}}, itad_api_key="K")
    deal = cheapshark.parse_deal(raw_deal("a", "Obscure"))
    assert app.itad_extras(ctx, deal, "Steam") == ()
    assert app.itad_extras(ctx, deal, "Steam") == ()
    assert len(ctx.session.calls) == 1


def test_deal_post_includes_extras_after_store():
    deal = cheapshark.parse_deal(raw_deal("a", "G", sale="7.49"))
    text = posts.deal_post(deal, "GOG", extras=("⏳ До 31 жовтня, 18:00", "🏆 Історичний мінімум ціни"))
    assert text.index("🏪 GOG") < text.index("⏳ До 31 жовтня") < text.index("🏆") < text.index("👉")
