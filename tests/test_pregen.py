from datetime import datetime, timezone

from lutserii import main as app
from lutserii.ai import PROMPT_VERSION
from lutserii.sources import anilist, cheapshark, gamerpower, steam

NOW = datetime(2026, 10, 27, 5, 0, tzinfo=timezone.utc).timestamp()


class CountingEditor:
    def __init__(self, db, fail_after=None):
        self.db = db
        self.calls = []
        self.failed = False
        self.fail_after = fail_after

    def blurb(self, kind, title, text, key):
        self.failed = False
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            self.failed = True
            return None
        self.calls.append(key)
        self.db.set_cache(f"v{PROMPT_VERSION}:{key}", f"Опис {title}.")
        return f"Опис {title}."


def giveaway(i):
    return {"id": i, "title": f"Game {i} Giveaway", "status": "Active", "description": f"Free stuff {i}",
            "published_date": "2026-10-10 10:00:00", "open_giveaway_url": f"https://g/{i}"}


def anime_item(media_id, pop):
    return {"airingAt": int(NOW) + 3600, "episode": 1, "media": {
        "id": media_id, "siteUrl": "u", "format": "TV", "countryOfOrigin": "JP", "isAdult": False,
        "popularity": pop, "description": f"Plot {media_id}", "title": {"romaji": f"Show {media_id}"}}}


def routes(n_giveaways=3):
    return {
        gamerpower.API: [giveaway(i) for i in range(1, n_giveaways + 1)],
        anilist.API: {"data": {"Page": {"pageInfo": {"hasNextPage": False},
                                         "airingSchedules": [anime_item(1, 90000), anime_item(2, 50000)]}}},
        f"{cheapshark.API}/stores": [{"storeID": "1", "storeName": "Steam"}],
        f"{cheapshark.API}/deals": [],
    }


def test_pregen_warms_only_missing_descriptions(make_ctx, db):
    ctx, _, _ = make_ctx(routes())
    ctx.clock = lambda: NOW
    ctx.editor = CountingEditor(db)
    assert app.run_pregen(ctx) == 5  # 2 аніме + 3 роздачі
    assert sorted(ctx.editor.calls) == ["ai:anime:1", "ai:anime:2", "ai:free:1", "ai:free:2", "ai:free:3"]
    assert app.run_pregen(ctx) == 0  # усе вже в кеші: звернень до ШІ немає


def test_pregen_respects_budget_and_posted_giveaways(make_ctx, db):
    db.mark_posted("free:1", "free")
    ctx, _, _ = make_ctx(routes(), pregen_max=3)
    ctx.clock = lambda: NOW
    ctx.editor = CountingEditor(db)
    assert app.run_pregen(ctx) == 3
    assert "ai:free:1" not in ctx.editor.calls


def test_pregen_stops_when_ai_runs_out(make_ctx, db):
    ctx, _, logs = make_ctx(routes(), pregen_max=10)
    ctx.clock = lambda: NOW
    ctx.editor = CountingEditor(db, fail_after=1)
    app.run_pregen(ctx)
    assert len(ctx.editor.calls) == 1
    assert any("зупиняюсь" in line for line in logs)


def test_pregen_without_ai_does_nothing(make_ctx):
    ctx, _, logs = make_ctx({})
    assert app.run_pregen(ctx) == 0 and any("немає ключа" in line for line in logs)


class FixedEditor:
    def __init__(self, provider, text):
        self.provider, self.text, self.calls = provider, text, []

    def blurb(self, kind, title, source, key):
        self.calls.append(key)
        return self.text


def test_compare_shows_both_models_side_by_side(make_ctx):
    previews = []
    ctx, _, _ = make_ctx(routes(2))
    ctx.clock = lambda: NOW
    ctx.editor, ctx.alt_editor = FixedEditor("anthropic", "Текст Claude."), FixedEditor("gemini", "Текст Gemini.")
    ctx.preview = lambda text, photos: previews.append(text)
    app.time.sleep = lambda s: None
    assert app.run_compare(ctx) == 4
    assert all("Claude:</b> Текст Claude." in t and "Gemini:</b> Текст Gemini." in t for t in previews)
    assert ctx.editor.calls[0] == "ai:cmp:Claude:a1" and ctx.alt_editor.calls[0] == "ai:cmp:Gemini:a1"


def test_compare_needs_both_keys(make_ctx):
    previews = []
    ctx, _, _ = make_ctx({})
    ctx.preview = lambda text, photos: previews.append(text)
    ctx.editor = FixedEditor("gemini", "x")
    assert app.run_compare(ctx) == 0 and "обидва ключі" in previews[0]
