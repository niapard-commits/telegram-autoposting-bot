from conftest import FakeResponse

from lutserii import main as main_mod
from lutserii.ai import GEMINI_API, Editor
from lutserii.main import run_task

OK = {"candidates": [{"content": {"parts": [{"text": "ок"}]}}]}


class KeySession:
    """Віддає відповіді по черзі й запам'ятовує, яким ключем зроблено запит."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.used = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.used.append(headers["x-goog-api-key"])
        reply = self.replies.pop(0) if self.replies else OK
        return reply if isinstance(reply, FakeResponse) else FakeResponse(reply)


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def make_editor(db, session, keys=("k1", "k2"), rpm=5, logs=None):
    clock = Clock()
    editor = Editor(
        session, keys[0], "gm", db, (logs.append if logs is not None else print), "gemini",
        extra_keys=tuple(keys[1:]), rpm=rpm, clock=clock.time, sleep=clock.sleep,
    )
    return editor, clock


def test_uses_first_key_until_minute_limit_then_second(db):
    session = KeySession([])
    editor, clock = make_editor(db, session, rpm=2)
    for _ in range(5):
        assert editor.ask("s", "u", 10) == "ок"
    assert session.used == ["k1", "k1", "k2", "k2", "k1"]
    assert clock.slept  # п'ятий запит довелося почекати, поки звільниться ключ


def test_switches_key_on_429(db):
    logs = []
    session = KeySession([FakeResponse("RESOURCE_EXHAUSTED PerMinute", 429)])
    editor, clock = make_editor(db, session, logs=logs)
    assert editor.ask("s", "u", 10) == "ок"
    assert session.used == ["k1", "k2"]
    assert any("ключ 1" in line for line in logs) and not any("k1" in line for line in logs)
    assert clock.slept == []  # переключились без очікування


def test_daily_limit_retires_key_for_the_run(db):
    session = KeySession([FakeResponse("quota PerDay exceeded", 429)])
    editor, _ = make_editor(db, session)
    editor.ask("s", "u", 10)
    editor.ask("s", "u", 10)
    assert session.used == ["k1", "k2", "k2"]


def test_all_keys_exhausted_returns_none(db):
    logs = []
    day = FakeResponse("PerDay", 429)
    session = KeySession([day, day])
    editor, _ = make_editor(db, session, logs=logs)
    assert editor.ask("s", "u", 10) is None
    assert "GeminiLimit" in logs[-1]


def test_single_key_waits_then_retries(db):
    session = KeySession([FakeResponse("PerMinute", 429)])
    editor, clock = make_editor(db, session, keys=("only",))
    assert editor.ask("s", "u", 10) == "ок"
    assert session.used == ["only", "only"]
    assert 55 <= sum(clock.slept) <= 70


# ---------- перегляд у приват ----------

def test_dry_run_sends_preview(make_ctx):
    ctx, sent, logs = make_ctx({}, dry_run=True)
    previews = []
    ctx.preview = lambda text, photos: previews.append((text, photos))
    ctx.publish("deal", ["k"], "<b>Пост</b>", ["https://img/1.jpg"])
    assert sent == []  # у канал нічого
    assert previews == [("<b>Пост</b>", ["https://img/1.jpg"])]


def test_preview_failure_does_not_break_run(make_ctx):
    ctx, _, logs = make_ctx({}, dry_run=True)

    def boom(text, photos):
        raise RuntimeError("chat not found")

    ctx.preview = boom
    ctx.publish("deal", ["k"], "Пост", [])
    assert any("не надіслано" in line for line in logs)


def test_run_task_announces_preview(make_ctx):
    ctx, sent, logs = make_ctx({}, dry_run=True, chat_id="@lut_i_serii")
    previews = []
    ctx.preview = lambda text, photos: previews.append(text)
    run_task(ctx, "free")  # звичайний перегляд починається з пояснення
    assert previews and previews[0].startswith("🧪 Перегляд")
    assert sent == []


def test_test_task_sends_live_samples_with_covers(make_ctx):
    now = 1_800_000_000
    deal = {"dealID": "d1", "title": "Real Game", "salePrice": "5.00", "normalPrice": "25.00",
            "savings": "80", "dealRating": "9.5", "storeID": "1", "steamAppID": "620"}
    media = {"id": 7, "siteUrl": "https://anilist.co/anime/7", "format": "TV", "countryOfOrigin": "JP",
             "isAdult": False, "popularity": 500, "genres": ["Comedy"], "description": "Plot.",
             "coverImage": {"extraLarge": "https://c/7.jpg"},
             "title": {"romaji": "Tiny Show", "english": None, "native": None}}
    page = {"data": {"Page": {"pageInfo": {"hasNextPage": False},
                              "airingSchedules": [{"airingAt": now + 3600, "episode": 2, "media": media}]}}}
    giveaway = {"id": 5, "title": "Free Game Giveaway", "status": "Active", "published_date": "2026-10-10 10:00:00",
                "open_giveaway_url": "https://g/5", "image": "https://img/free.jpg"}
    ctx, sent, logs = make_ctx(
        {
            "https://www.cheapshark.com/api/1.0/stores": [{"storeID": "1", "storeName": "Steam"}],
            "https://www.cheapshark.com/api/1.0/deals": [deal],
            "https://www.gamerpower.com/api/giveaways": [giveaway],
            main_mod.anilist.API: page,
        },
        chat_id="@lut_i_serii",
    )
    ctx.clock = lambda: now
    got = []
    ctx.preview = lambda text, photos: got.append((text, photos))
    assert run_task(ctx, "test") == []
    texts = [t for t, _ in got]
    assert texts[0].startswith("✅ Бот підключено")
    assert any("Real Game" in t for t in texts)                       # справжня назва
    assert any(p == ["https://img/free.jpg"] for _, p in got)          # картинка роздачі
    assert any(p == ["https://c/7.jpg"] for _, p in got)               # обкладинка аніме
    assert any(t.startswith("ℹ️ Озвучка") for t in texts)              # без ключа ШІ: чесне пояснення
    assert sent == [] and ctx.db.count_kind("deal") == 0


# ---------- перевантажена модель (503) ----------

class ModelSession:
    def __init__(self, replies):
        self.replies = replies  # модель -> відповідь (або список відповідей)
        self.used = []

    def post(self, url, headers=None, json=None, timeout=None):
        model = url.split("/models/")[1].split(":")[0]
        self.used.append(model)
        reply = self.replies[model]
        return reply if isinstance(reply, FakeResponse) else FakeResponse(reply)


def make_model_editor(db, session, logs, fallbacks=("gm2",)):
    clock = Clock()
    editor = Editor(session, "k", "gm", db, logs.append, "gemini",
                    clock=clock.time, sleep=clock.sleep, fallback_models=fallbacks)
    return editor, clock


def test_overloaded_model_switches_to_fallback_and_sticks(db):
    logs = []
    session = ModelSession({"gm": FakeResponse({}, 503), "gm2": OK})
    editor, clock = make_model_editor(db, session, logs)
    assert editor.ask("s", "u", 10) == "ок"
    assert session.used == ["gm", "gm", "gm2"]
    assert any("gm перевантажена, беру gm2" in line for line in logs)
    assert clock.slept == [3]
    assert editor.ask("s", "u", 10) == "ок"
    assert session.used[-1] == "gm2" and session.used.count("gm") == 2  # далі одразу резервна
    assert editor.active_model == "gm2"


def test_all_models_overloaded_returns_none_with_code(db):
    logs = []
    session = ModelSession({"gm": FakeResponse({}, 503), "gm2": FakeResponse({}, 503)})
    editor, _ = make_model_editor(db, session, logs)
    assert editor.ask("s", "u", 10) is None
    assert "код 503" in logs[-1]


def test_overload_without_fallbacks_retries_then_fails(db):
    logs = []
    session = ModelSession({"gm": FakeResponse({}, 503)})
    editor, clock = make_model_editor(db, session, logs, fallbacks=())
    assert editor.ask("s", "u", 10) is None
    assert session.used == ["gm", "gm", "gm"] and clock.slept == [3, 6]
    assert "код 503" in logs[-1]


def test_missing_model_404_switches_to_next(db):
    logs = []
    session = ModelSession({"gm": FakeResponse({}, 404), "gm2": OK})
    editor, clock = make_model_editor(db, session, logs)
    assert editor.ask("s", "u", 10) == "ок"
    assert session.used == ["gm", "gm2"] and clock.slept == []
    assert any("gm не знайдено, беру gm2" in line for line in logs)


# ---------- відкладення постів, коли ШІ тимчасово недоступний ----------

from datetime import datetime as _dt, timezone as _tz  # noqa: E402

from lutserii import main as _app  # noqa: E402
from lutserii.sources import anilist as _anilist, gamerpower as _gp  # noqa: E402


class FlakyEditor:
    def __init__(self, fail=True):
        self.fail = fail
        self.failed = False

    def blurb(self, kind, title, text, key):
        self.failed = self.fail
        return None if self.fail else "Готовий опис."


def _giveaway():
    return [{"id": 1, "title": "Game Giveaway", "status": "Active", "published_date": "2026-10-10 10:00:00",
             "open_giveaway_url": "https://g/1", "description": "Plot."}]


def test_free_post_deferred_then_published_without_blurb_after_max_defers(make_ctx):
    ctx, sent, logs = make_ctx({_gp.API: _giveaway()})
    ctx.editor = FlakyEditor()
    for _ in range(_app.MAX_DEFERS):
        assert _app.run_free(ctx) == 0
    assert sent == [] and any("відкладаю" in line for line in logs)
    assert _app.run_free(ctx) == 1  # чекали достатньо: публікуємо без опису
    assert "Готовий опис" not in sent[0][0]


def test_free_post_gets_blurb_when_ai_recovers(make_ctx):
    ctx, sent, _ = make_ctx({_gp.API: _giveaway()})
    ctx.editor = FlakyEditor()
    assert _app.run_free(ctx) == 0
    ctx.editor.fail = False
    assert _app.run_free(ctx) == 1
    assert "Готовий опис." in sent[0][0]


def test_anime_cards_deferred_and_completed_next_run(make_ctx):
    now = _dt(2026, 10, 27, 5, 0, tzinfo=_tz.utc).timestamp()

    def item(offset, ep, popularity, title, media_id):
        return {"airingAt": int(now) + offset, "episode": ep, "media": {
            "id": media_id, "siteUrl": "https://a/1", "format": "TV", "countryOfOrigin": "JP",
            "isAdult": False, "popularity": popularity, "genres": [], "description": "Plot.",
            "coverImage": {"extraLarge": f"https://c/{media_id}.jpg"},
            "title": {"romaji": title, "english": None, "native": None}}}

    page = {"data": {"Page": {"pageInfo": {"hasNextPage": False}, "airingSchedules": [
        item(3600, 1, 90000, "Big", 1), item(7200, 2, 60000, "Mid", 2)]}}}
    ctx, sent, _ = make_ctx({_anilist.API: page}, anime_highlights=2, anime_digest=True)
    ctx.clock = lambda: now
    ctx.editor = FlakyEditor()
    assert _app.run_anime(ctx) == 2           # добірка вийшла, картки відкладено
    assert len(sent) == 1
    ctx.editor.fail = False
    assert _app.run_anime(ctx) == 2           # картки допубліковано, добірка не дублюється
    assert len(sent) == 3 and all("Готовий опис." in t for t, _ in sent[1:])
    assert _app.run_anime(ctx) == 0


def test_rejected_key_401_switches_to_next_and_is_reported(db):
    logs = []
    session = KeySession([FakeResponse({"error": "bad"}, 401)])
    editor, _ = make_editor(db, session, logs=logs)
    assert editor.ask("s", "u", 10) == "ок"
    assert session.used == ["k1", "k2"]
    assert any("ключ 1 відхилено" in line and "401" in line for line in logs)
    editor.ask("s", "u", 10)
    assert session.used == ["k1", "k2", "k2"]  # відхилений ключ більше не беремо


def test_all_keys_rejected_returns_none(db):
    bad = FakeResponse({}, 401)
    session = KeySession([bad, bad])
    editor, _ = make_editor(db, session)
    assert editor.ask("s", "u", 10) is None and editor.failed


def test_blurb_falls_back_to_finished_legacy_cache_when_ai_is_down(db):
    db.set_cache("ai:anime:1", "Герой отримує другий шанс. Він не здається.")
    db.set_cache("ai:anime:2", "Обірваний опис без крапки")
    session = KeySession([FakeResponse({}, 401), FakeResponse({}, 401), FakeResponse({}, 401),
                          FakeResponse({}, 401)])
    editor, _ = make_editor(db, session)
    assert editor.blurb("anime", "T", "source text", "ai:anime:1") == "Герой отримує другий шанс. Він не здається."
    assert editor.blurb("anime", "T", "source text", "ai:anime:2") is None
