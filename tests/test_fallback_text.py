from conftest import FakeResponse
from test_enrich import AI_API, ai_response

from lutserii import main as app
from lutserii.ai import Editor
from lutserii.sources import jikan

MAL = jikan.API.format(77)


def claude(text_by_marker):
    """Підробка Claude: відповідь залежить від того, що є в описі."""
    def route(payload):
        prompt = payload["messages"][0]["content"]
        for marker, answer in text_by_marker.items():
            if marker in prompt:
                return ai_response(answer)
        return ai_response("-")
    return route


def _ctx(make_ctx, routes):
    ctx, _, logs = make_ctx(routes)
    ctx.editor = Editor(ctx.session, "key", "model", ctx.db, logs.append, sleep=lambda s: None)
    return ctx


def test_parse_synopsis_skips_adult_and_cleans_source():
    ok = {"data": {"synopsis": "A hero rises. [Written by MAL Rewrite]", "rating": "PG-13"}}
    assert jikan.parse_synopsis(ok) == "A hero rises."
    assert jikan.parse_synopsis({"data": {"synopsis": "x", "rating": "Rx - Hentai"}}) == ""
    assert jikan.parse_synopsis({"data": None}) == "" and jikan.parse_synopsis("oops") == ""


def test_anime_without_plot_uses_myanimelist(make_ctx):
    routes = {AI_API: claude({"A hero rises": "Герой повстає проти імперії."}), MAL: {"data": {"synopsis": "A hero rises against an empire.", "rating": "PG-13"}}}
    ctx = _ctx(make_ctx, routes)
    out = app.ai_blurb(ctx, "anime", "Show", "Episode list, staff, release notes.", "ai:anime:5", "k",
                       alt_text=lambda: app.mal_synopsis(ctx, 77))
    assert out == "Герой повстає проти імперії."
    # і запасний текст, і відмова «-» кешуються: другий виклик не ходить ні в ШІ, ні в MAL
    calls = len(ctx.session.calls)
    assert app.ai_blurb(ctx, "anime", "Show", "Episode list, staff, release notes.", "ai:anime:5", "k",
                        alt_text=lambda: app.mal_synopsis(ctx, 77)) == "Герой повстає проти імперії."
    assert len(ctx.session.calls) == calls


def test_no_fallback_when_primary_works_or_ai_is_down(make_ctx):
    routes = {AI_API: claude({"Primary plot": "Гра про торгівлю у космосі."})}
    ctx = _ctx(make_ctx, routes)
    asked = []
    assert app.ai_blurb(ctx, "game", "G", "Primary plot here.", "ai:g:1", "k",
                        alt_text=lambda: asked.append(1) or "x") == "Гра про торгівлю у космосі."
    assert not asked
    down = _ctx(make_ctx, {AI_API: FakeResponse({}, 500)})
    try:
        app.ai_blurb(down, "game", "G", "Some text.", "ai:g:2", "k2", alt_text=lambda: asked.append(2) or "x")
    except app.DeferPost:
        pass
    assert not asked  # ШІ недоступний, тож шукати інший текст немає сенсу


def test_empty_source_still_tries_fallback_and_survives_its_failure(make_ctx):
    ctx = _ctx(make_ctx, {AI_API: claude({"Fallback plot": "Запасний сюжет."})})
    assert app.ai_blurb(ctx, "game", "G", "", "ai:g:3", "k", alt_text=lambda: "Fallback plot text.") == "Запасний сюжет."

    def boom():
        raise RuntimeError("network")

    assert app.ai_blurb(ctx, "game", "G", "", "ai:g:4", "k", alt_text=boom) is None


def test_compare_runs_every_contender(make_ctx, monkeypatch):
    from lutserii.sources import anilist, gamerpower
    monkeypatch.setattr(anilist, "fetch_airing", lambda *a, **k: [])
    monkeypatch.setattr(gamerpower, "fetch_giveaways", lambda s: [
        type("G", (), {"title": "Game", "description": "A space game about trading.", "id": 1})()])
    ctx, _, _ = make_ctx({})
    previews = []
    ctx.preview = lambda text, photos: previews.append(text)

    class Fixed:
        provider = "x"
        last_reason = ""

        def __init__(self, text):
            self.text = text

        def blurb(self, *a):
            return self.text

    ctx.compare_editors = [("A", Fixed("Перша версія.")), ("B", Fixed("Друга версія.")), ("C", Fixed("Третя версія."))]
    assert app.run_compare(ctx) == 1
    assert all(x in previews[0] for x in ("Перша", "Друга", "Третя", "<b>B:</b>"))
